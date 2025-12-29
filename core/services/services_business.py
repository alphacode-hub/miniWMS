# core/services/services_business.py
"""
services_business.py – ORBION SaaS (enterprise, baseline aligned)

✔ Crea negocio + admin (tenant customer)
✔ Soporta tenant system (ORBION / superadmin) sin suscripciones comerciales
✔ Provisiona suscripción inicial (INBOUND como módulo de entrada) RESPETANDO contrato V1
✔ Audita eventos relevantes (baseline v2.1)
✔ No depende de rutas ni UI

=========================================================
BASELINE (2025-12-29)
=========================================================
- TRIAL NO crea período comercial (cumple ck_suscripcion_trial_vs_periodo_exclusivo).
- Entitlements: fuente de provisioning (enabled/coming_soon); acceso real = overlay de SuscripcionModulo (snapshot).
- Se elimina legacy (plan_tipo fallback) de este servicio.
- Tenant system: usuario global (negocio_id=None) si el schema lo permite; si no, fallback seguro al negocio.id.
- Auditoría: 1 evento usuario.create.* + 1 evento auth.signup.ok (sin duplicar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from core.logging_config import logger
from core.models import Negocio, Usuario
from core.models.enums import ModuleKey, NegocioEstado, SubscriptionStatus
from core.models.saas import SuscripcionModulo
from core.models.time import utcnow
from core.security import hash_password
from core.services.services_audit import AuditAction, audit


# =========================================================
# CONFIG / HELPERS
# =========================================================

@dataclass(frozen=True)
class ProvisioningPolicy:
    """
    Policy explícita para evitar “magia”.

    Nota:
    - inbound es el punto de entrada (provisionado por defecto en customer)
    - el resto de módulos, si aún no existen comercialmente, deberían ir coming_soon desde entitlements
      (eso vive en services_entitlements / Negocio.entitlements).
    """
    segment: str = "emprendedor"
    inbound_enabled: bool = True
    wms_enabled: bool = False

    # Trial por defecto
    trial_days: int = 14

    # Período comercial (solo aplica si start_trial=False)
    billing_period_days: int = 30


def _normalize_segment(seg: str) -> str:
    s = (seg or "").strip().lower()
    aliases = {
        "entrepreneur": "emprendedor",
        "startup": "emprendedor",
        "smb": "pyme",
        "pymes": "pyme",
        "mid": "pyme",
        "midmarket": "pyme",
        "ent": "enterprise",
        "corp": "enterprise",
        "corporate": "enterprise",
        # tenant system (superadmin/orbion)
        "superadmin": "superadmin",
        "system": "superadmin",
        "orbion": "superadmin",
    }
    s = aliases.get(s, s)
    if s not in {"emprendedor", "pyme", "enterprise", "superadmin"}:
        raise ValueError("Segmento inválido.")
    return s


def _default_entitlements_for_policy(policy: ProvisioningPolicy, *, tenant_type: str) -> dict:
    """
    Entitlements base para negocio recién creado.

    Importante:
    - Entitlements define provisioning (enabled/status/coming_soon).
    - Acceso real se calcula en snapshot (services_entitlements) usando overlay SuscripcionModulo.
    - Aquí marcamos módulos no trabajados como coming_soon=True para que el Hub los muestre como "Próximamente"
      sin opción de suscribirse (esa decisión es de UI, pero el flag vive aquí).
    """
    tt = (tenant_type or "customer").strip().lower()

    if tt == "system":
        # Tenant system: negocio técnico (si lo usas), sin módulos comerciales
        return {
            "segment": "superadmin",
            "modules": {
                "core": {"enabled": True, "status": "active", "coming_soon": False},
                "inbound": {"enabled": False, "status": "inactive", "coming_soon": True},
                "wms": {"enabled": False, "status": "inactive", "coming_soon": True},
                "analytics_plus": {"enabled": False, "status": "inactive", "coming_soon": True},
                "ml_ia": {"enabled": False, "status": "inactive", "coming_soon": True},
            },
            "limits": {},
            "billing": {"source": "baseline"},
        }

    # customer
    return {
        "segment": policy.segment,
        "modules": {
            "core": {"enabled": True, "status": "active", "coming_soon": False},
            "inbound": {
                "enabled": bool(policy.inbound_enabled),
                # Nota: status "active" indica provisionado; acceso real depende de SuscripcionModulo (trial/active)
                "status": "active" if policy.inbound_enabled else "inactive",
                "coming_soon": False,
            },
            # módulos no trabajados: Próximamente (no suscribible aún)
            "wms": {
                "enabled": bool(policy.wms_enabled),
                "status": "active" if policy.wms_enabled else "inactive",
                "coming_soon": True if not policy.wms_enabled else False,
            },
            "analytics_plus": {"enabled": False, "status": "inactive", "coming_soon": True},
            "ml_ia": {"enabled": False, "status": "inactive", "coming_soon": True},
        },
        "limits": {},
        "billing": {"source": "baseline"},
    }


def _ensure_no_duplicate_subscription(db: Session, *, negocio_id: int, mk: ModuleKey) -> bool:
    exists = (
        db.query(SuscripcionModulo.id)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    return bool(exists)


def _provision_subscription(
    db: Session,
    *,
    negocio_id: int,
    mk: ModuleKey,
    policy: ProvisioningPolicy,
    start_trial: bool,
) -> Optional[SuscripcionModulo]:
    """
    Crea SuscripcionModulo si no existe.

    ✅ CONTRATO V1 (crítico):
    - TRIAL:
        status=TRIAL
        trial_ends_at != None
        current_period_start/end = None
        next_renewal_at = None
        last_payment_at = None
        past_due_since = None
    - PAID (start_trial=False):
        status=ACTIVE
        trial_ends_at = None
        current_period_start/end != None
        next_renewal_at = current_period_end
        last_payment_at = now
    """
    if _ensure_no_duplicate_subscription(db, negocio_id=negocio_id, mk=mk):
        return None

    now = utcnow()

    if start_trial:
        trial_days = max(1, int(policy.trial_days or 14))
        trial_ends = now + timedelta(days=trial_days)

        sub = SuscripcionModulo(
            negocio_id=negocio_id,
            module_key=mk,
            status=SubscriptionStatus.TRIAL,
            started_at=now,
            trial_ends_at=trial_ends,
            current_period_start=None,
            current_period_end=None,
            next_renewal_at=None,
            last_payment_at=None,
            past_due_since=None,
            cancel_at_period_end=0,
            cancelled_at=None,
        )
        db.add(sub)
        db.flush()
        return sub

    # PAID
    period_days = max(1, int(policy.billing_period_days or 30))
    period_start = now
    period_end = now + timedelta(days=period_days)

    sub = SuscripcionModulo(
        negocio_id=negocio_id,
        module_key=mk,
        status=SubscriptionStatus.ACTIVE,
        started_at=now,
        trial_ends_at=None,
        current_period_start=period_start,
        current_period_end=period_end,
        next_renewal_at=period_end,
        last_payment_at=now,
        past_due_since=None,
        cancel_at_period_end=0,
        cancelled_at=None,
    )
    db.add(sub)
    db.flush()
    return sub


def _safe_audit(
    db: Session,
    *,
    action: AuditAction,
    user: dict,
    negocio_id: int | None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    extra: dict | None = None,
    commit: bool = False,
) -> None:
    """
    Auditoría best-effort: nunca debe romper el flujo.
    """
    try:
        audit(
            db,
            action=action,
            user=user,
            negocio_id=negocio_id,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            extra=extra,
            commit=commit,
        )
    except Exception:
        # no loguear con exception stack para no ensuciar; esto es best-effort
        logger.warning("[AUDIT] failed action=%s negocio_id=%s", getattr(action, "value", str(action)), negocio_id)


# =========================================================
# API PÚBLICA
# =========================================================

def crear_negocio_con_admin(
    db: Session,
    *,
    nombre_negocio: str,
    whatsapp: str | None,
    email_admin: str,
    password_admin: str,
    nombre_admin: str,
    tenant_type: str = "customer",     # "customer" | "system"
    segment: str = "emprendedor",      # emprendedor|pyme|enterprise|superadmin
    provision_inbound: bool = True,
    provision_wms: bool = False,
    start_trial: bool = True,
) -> Tuple[Negocio, Usuario]:
    """
    Crea un negocio y su usuario admin inicial, con provisioning SaaS.

    Contrato operativo (baseline):
    - Tenant "customer": crea entitlements + (opcional) suscripciones comerciales por módulo.
    - Tenant "system": NO crea suscripciones comerciales (se usa para ORBION/superadmin).

    Auditoría (baseline v2.1):
    - NEGOCIO_STATE_UPDATE (creación -> activo)
    - AUTH_LOGIN_OK (usuario.create.*)
    - MODULE_ACTIVATE (si se provisiona suscripción)
    - AUTH_LOGIN_OK (auth.signup.ok)
    - AUTH_LOGIN_FAIL (auth.signup.fail)
    """
    actor_user = {"email": email_admin}

    tt = (tenant_type or "customer").strip().lower()
    if tt not in {"customer", "system"}:
        tt = "customer"

    seg = _normalize_segment(segment)
    if tt == "system":
        seg = "superadmin"

    policy = ProvisioningPolicy(
        segment=seg,
        inbound_enabled=bool(provision_inbound) if tt == "customer" else False,
        wms_enabled=bool(provision_wms) if tt == "customer" else False,
    )

    try:
        # ----------------------------
        # Crear negocio
        # ----------------------------
        negocio = Negocio(
            nombre_fantasia=nombre_negocio,
            whatsapp_notificaciones=whatsapp,
            estado=NegocioEstado.ACTIVO,
        )

        # Si tu modelo tiene tenant_type, lo seteamos
        if hasattr(negocio, "tenant_type"):
            setattr(negocio, "tenant_type", tt)

        # Set explícito de entitlements (baseline, sin legacy)
        try:
            negocio.entitlements = _default_entitlements_for_policy(policy, tenant_type=tt)
        except Exception:
            # si entitlements fuera Text JSON en algún entorno legacy (no debería en baseline actual)
            pass

        db.add(negocio)
        db.flush()  # negocio.id

        _safe_audit(
            db,
            action=AuditAction.NEGOCIO_STATE_UPDATE,
            user={"email": email_admin, "negocio_id": negocio.id, "rol": ("admin" if tt == "customer" else "superadmin")},
            negocio_id=negocio.id if tt == "customer" else None,
            entity_type="negocio",
            entity_id=negocio.id,
            before={"estado": None},
            after={"estado": "activo"},
            extra={"nombre_fantasia": nombre_negocio, "tenant_type": tt, "segment": seg},
            commit=False,
        )

        # ----------------------------
        # Crear admin / superadmin
        # ----------------------------
        # Regla:
        # - customer -> usuario con negocio_id = negocio.id
        # - system   -> usuario global (negocio_id=None) si el schema lo permite; si no, fallback a negocio.id
        desired_negocio_id: int | None = (negocio.id if tt == "customer" else None)

        usuario_admin = Usuario(
            negocio_id=desired_negocio_id,
            email=email_admin,
            password_hash=hash_password(password_admin),
            rol="admin" if tt == "customer" else "superadmin",
            activo=1,
            nombre_mostrado=nombre_admin,
        )
        db.add(usuario_admin)

        try:
            db.flush()
        except Exception:
            # schema que NO permite negocio_id NULL -> fallback (solo para tenant system)
            if tt != "system":
                raise
            db.rollback()
            # Re-attach negocio (rollback limpia la sesión de pending)
            negocio = db.query(Negocio).filter(Negocio.id == negocio.id).first()  # type: ignore[union-attr]
            if not negocio:
                raise

            usuario_admin = Usuario(
                negocio_id=negocio.id,
                email=email_admin,
                password_hash=hash_password(password_admin),
                rol="superadmin",
                activo=1,
                nombre_mostrado=nombre_admin,
            )
            db.add(usuario_admin)
            db.flush()

        _safe_audit(
            db,
            action=AuditAction.AUTH_LOGIN_OK,
            user={"email": email_admin, "negocio_id": (negocio.id if tt == "customer" else None), "rol": usuario_admin.rol},
            negocio_id=(negocio.id if tt == "customer" else None),
            entity_type="usuario",
            entity_id=getattr(usuario_admin, "id", None),
            extra={
                "event": "usuario.create.admin" if tt == "customer" else "usuario.create.superadmin",
                "email": email_admin,
                "rol": usuario_admin.rol,
                "nombre_mostrado": nombre_admin,
            },
            commit=False,
        )

        # ----------------------------
        # Provisioning SaaS (solo customer)
        # ----------------------------
        if tt == "customer":
            created_subs: list[SuscripcionModulo] = []

            if policy.inbound_enabled:
                sub_inb = _provision_subscription(
                    db,
                    negocio_id=negocio.id,
                    mk=ModuleKey.INBOUND,
                    policy=policy,
                    start_trial=bool(start_trial),
                )
                if sub_inb:
                    created_subs.append(sub_inb)
                    _safe_audit(
                        db,
                        action=AuditAction.MODULE_ACTIVATE,
                        user={"email": email_admin, "negocio_id": negocio.id, "rol": "admin"},
                        negocio_id=negocio.id,
                        entity_type="subscription",
                        entity_id=getattr(sub_inb, "id", None),
                        after={
                            "module": ModuleKey.INBOUND.value,
                            "status": getattr(sub_inb.status, "value", str(sub_inb.status)),
                            "trial": bool(getattr(sub_inb, "trial_ends_at", None)),
                            "trial_ends_at": (
                                str(getattr(sub_inb, "trial_ends_at", None))
                                if getattr(sub_inb, "trial_ends_at", None)
                                else None
                            ),
                            "period_end": (
                                str(getattr(sub_inb, "current_period_end", None))
                                if getattr(sub_inb, "current_period_end", None)
                                else None
                            ),
                        },
                        commit=False,
                    )

            if policy.wms_enabled:
                sub_wms = _provision_subscription(
                    db,
                    negocio_id=negocio.id,
                    mk=ModuleKey.WMS,
                    policy=policy,
                    start_trial=bool(start_trial),
                )
                if sub_wms:
                    created_subs.append(sub_wms)
                    _safe_audit(
                        db,
                        action=AuditAction.MODULE_ACTIVATE,
                        user={"email": email_admin, "negocio_id": negocio.id, "rol": "admin"},
                        negocio_id=negocio.id,
                        entity_type="subscription",
                        entity_id=getattr(sub_wms, "id", None),
                        after={
                            "module": ModuleKey.WMS.value,
                            "status": getattr(sub_wms.status, "value", str(sub_wms.status)),
                            "trial": bool(getattr(sub_wms, "trial_ends_at", None)),
                            "trial_ends_at": (
                                str(getattr(sub_wms, "trial_ends_at", None))
                                if getattr(sub_wms, "trial_ends_at", None)
                                else None
                            ),
                            "period_end": (
                                str(getattr(sub_wms, "current_period_end", None))
                                if getattr(sub_wms, "current_period_end", None)
                                else None
                            ),
                        },
                        commit=False,
                    )

            logger.info(
                "[BUSINESS] provisioning negocio_id=%s created_subs=%s",
                negocio.id,
                [(s.module_key.value, getattr(s.status, "value", str(s.status))) for s in created_subs],
            )

        # Auditoría: signup ok (canónica)
        _safe_audit(
            db,
            action=AuditAction.AUTH_LOGIN_OK,
            user={"email": email_admin, "negocio_id": (negocio.id if tt == "customer" else None), "rol": usuario_admin.rol},
            negocio_id=(negocio.id if tt == "customer" else None),
            extra={"event": "auth.signup.ok", "tenant_type": tt},
            commit=False,
        )

        db.commit()
        db.refresh(negocio)
        db.refresh(usuario_admin)

        logger.info(
            "[BUSINESS] negocio+admin creado negocio_id=%s tenant_type=%s admin=%s",
            negocio.id,
            tt,
            email_admin,
        )
        return negocio, usuario_admin

    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass

        # Auditoría fail (best effort)
        try:
            _safe_audit(
                db,
                action=AuditAction.AUTH_LOGIN_FAIL,
                user=actor_user,
                negocio_id=None,
                extra={
                    "event": "auth.signup.fail",
                    "email": email_admin,
                    "negocio_nombre": nombre_negocio,
                    "tenant_type": (tenant_type or "customer"),
                    "segment": (segment or "emprendedor"),
                    "error": str(exc),
                },
                commit=True,
            )
        except Exception:
            pass

        logger.exception("[BUSINESS] crear_negocio_con_admin failed email=%s", email_admin)
        raise
