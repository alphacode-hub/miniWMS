# core/services/services_audit.py
"""
Servicio de Auditoría v2.1 – ORBION (SaaS enterprise, baseline aligned)

✔ Source of truth para auditoría
✔ Independiente de rutas / UI
✔ Seguro ante fallos (never break flow)
✔ Compatible con:
  - superadmin global
  - impersonación
  - jobs / cron
✔ Contexto enterprise (before/after, request_id, ip, user_agent)
✔ Acciones canónicas (baseline SaaS)
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Auditoria
from core.models.time import utcnow


# =========================================================
# ACCIONES CANÓNICAS (BASELINE SaaS)
# =========================================================

class AuditAction:
    # --- Negocio
    NEGOCIO_SEGMENT_UPDATE = "negocio.segment.update"
    NEGOCIO_STATE_UPDATE = "negocio.state.update"

    # --- SaaS / módulos
    MODULE_ACTIVATE = "module.activate"
    MODULE_CANCEL_AT_PERIOD_END = "module.cancel_at_period_end"
    MODULE_UNSCHEDULE_CANCEL = "module.unschedule_cancel"
    MODULE_SUSPEND = "module.suspend"
    MODULE_RENEW_NOW = "module.renew_now"

    # --- Enforcement (nuevo, usado por services_enforcement.py)
    ENFORCEMENT_WARN = "enforcement.warn"
    ENFORCEMENT_BLOCK = "enforcement.block"

    # --- Seguridad / auth
    AUTH_LOGIN_OK = "auth.login.ok"
    AUTH_LOGIN_FAIL = "auth.login.fail"
    AUTH_LOGOUT = "auth.logout"

    # --- Impersonación
    IMPERSONATION_START = "impersonation.start"
    IMPERSONATION_STOP = "impersonation.stop"


# =========================================================
# HELPERS INTERNOS
# =========================================================

def _safe_json(obj: Any) -> str:
    """Serialización defensiva para payloads de auditoría."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "<non_serializable>"


def _resolve_negocio_id(
    db: Session,
    *,
    negocio_id: Optional[int],
    user: Optional[dict],
) -> Optional[int]:
    """
    Regla baseline (explícita, sin magia):
    - Si negocio_id explícito -> usarlo
    - Si user tiene negocio_id -> usarlo
    - Superadmin global SIN negocio -> NO audita
      (evitamos inventar negocios tipo 'Global')
    """
    if negocio_id:
        return int(negocio_id)

    if user and user.get("negocio_id"):
        return int(user["negocio_id"])

    return None


def _resolve_actor(user: Optional[dict], actor: Optional[str]) -> str:
    """Determina el actor humano/sistema de la acción."""
    if actor:
        return actor

    if user:
        return (
            user.get("email")
            or user.get("usuario")
            or user.get("user")
            or "sistema"
        )

    return "sistema"


# =========================================================
# HELPERS PÚBLICOS (útiles para rutas)
# =========================================================

def build_request_ctx(request: Request) -> dict:
    """
    Contexto defensivo para auditar requests HTTP.
    No es obligatorio (jobs / cron pueden omitirlo).
    """
    try:
        ip = request.client.host if request.client else None

        return {
            "ip": ip,
            "user_agent": request.headers.get("user-agent"),
            "request_id": (
                request.headers.get("x-request-id")
                or request.headers.get("x-correlation-id")
            ),
            "path": str(request.url.path),
            "method": request.method,
        }
    except Exception:
        return {}


def classify_audit_level(action: str, detalle: str | None = None) -> str:
    """
    Clasificación baseline-aligned para UI:
    critico | warning | info | normal
    """
    a = (action or "").strip().lower()

    if a in {
        AuditAction.AUTH_LOGIN_FAIL,
        AuditAction.MODULE_SUSPEND,
        AuditAction.NEGOCIO_STATE_UPDATE,
        AuditAction.ENFORCEMENT_BLOCK,
    }:
        return "critico"

    if a in {
        AuditAction.MODULE_CANCEL_AT_PERIOD_END,
        AuditAction.MODULE_UNSCHEDULE_CANCEL,
        AuditAction.ENFORCEMENT_WARN,
    }:
        return "warning"

    if a in {
        AuditAction.AUTH_LOGIN_OK,
        AuditAction.AUTH_LOGOUT,
        AuditAction.MODULE_ACTIVATE,
        AuditAction.MODULE_RENEW_NOW,
        AuditAction.IMPERSONATION_START,
        AuditAction.IMPERSONATION_STOP,
        AuditAction.NEGOCIO_SEGMENT_UPDATE,
    }:
        return "info"

    return "normal"


# =========================================================
# API PÚBLICA
# =========================================================

def audit(
    db: Session,
    *,
    action: str,
    user: Optional[dict] = None,
    actor: Optional[str] = None,
    negocio_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    extra: Optional[dict] = None,
    request_ctx: Optional[dict] = None,
    payload: Any = None,  # 👈 compat: servicios nuevos pueden enviar payload directo
    commit: bool = False,
) -> None:
    """
    Registra un evento de auditoría enterprise.

    ✔ Nunca lanza excepción hacia arriba
    ✔ Por defecto usa flush (no commit)
    ✔ request_ctx es opcional
    ✔ Si no se puede resolver negocio_id → no audita (no rompe flujo)
    ✔ Usa SAVEPOINT para no afectar la transacción del caller si falla
    """
    try:
        nid = _resolve_negocio_id(db, negocio_id=negocio_id, user=user)
        if not nid:
            return

        # Si viene payload, lo usamos como "detalle" directo (modo flexible).
        # Si no, construimos payload estándar con before/after/extra.
        if payload is not None:
            detalle = _safe_json(payload)
        else:
            pack: dict[str, Any] = {
                "action": action,
                "entity": {"type": entity_type, "id": entity_id},
                "before": before,
                "after": after,
                "extra": extra or {},
            }
            if request_ctx:
                pack["request"] = request_ctx
            detalle = _safe_json(pack)

        reg = Auditoria(
            negocio_id=nid,
            usuario=_resolve_actor(user, actor),
            accion=action,
            detalle=detalle,
            fecha=utcnow(),
        )

        # SAVEPOINT: si el insert de auditoría falla, no revienta la transacción del caller.
        with db.begin_nested():
            db.add(reg)
            db.flush()

        if commit:
            db.commit()

    except Exception as exc:
        # Importante: NO hacemos rollback global aquí.
        logger.error("[AUDIT][ERROR] action=%s error=%s", action, exc)


def audit_safe_commit(
    db: Session,
    *,
    action: str,
    user: Optional[dict] = None,
    actor: Optional[str] = None,
    negocio_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    extra: Optional[dict] = None,
    request_ctx: Optional[dict] = None,
    payload: Any = None,
) -> None:
    """
    Variante explícita para jobs / cron:
    - intenta auditar y commitear
    - si falla, no rompe flujo
    """
    audit(
        db,
        action=action,
        user=user,
        actor=actor,
        negocio_id=negocio_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        extra=extra,
        request_ctx=request_ctx,
        payload=payload,
        commit=True,
    )
