# core/services/services_business.py
"""
services_business.py – ORBION SaaS (enterprise, baseline aligned)

✔ Crea negocio + admin (tenant customer)
✔ Soporta tenant system (ORBION / superadmin) sin suscripciones comerciales
✔ Provisiona suscripción inicial (INBOUND como módulo de entrada)
✔ Audita eventos relevantes (baseline v2.1)
✔ No depende de rutas ni UI
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
    - inbound es el punto de entrada (enabled/activo)
    - wms queda apagado por defecto (se activa luego)
    """
    segment: str = "emprendedor"
    inbound_enabled: bool = True
    wms_enabled: bool = False

    # Trial por defecto (puedes ajustar a 7/14/30)
    trial_days: int = 14
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
        # nuevo contrato: tenant system (superadmin/orbion)
        "superadmin": "superadmin",
        "system": "superadmin",
        "orbion": "superadmin",
    }
    s = aliases.get(s, s)
    if s not in {"emprendedor", "pyme", "enterprise", "superadmin"}:
        raise ValueError("Segmento inválido.")
    return s


def _default_entitlements_for_policy(policy: ProvisioningPolicy) -> dict:
    # Entitlements base (source of truth de límites vive en services_entitlements)
    return {
        "segment": policy.segment,
        "modules": {
            "core": {"enabled": True, "status": "active"},
            "inbound": {"enabled": bool(policy.inbound_enabled), "status": "active" if policy.inbound_enabled else "inactive"},
            "wms": {"enabled": bool(policy.wms_enabled), "status": "active" if policy.wms_enabled else "inactive"},
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
    - Periodos rolling (30 días)
    - Trial opcional (trial_ends_at)
    """
    if _ensure_no_duplicate_subscription(db, negocio_id=negocio_id, mk=mk):
        return None

    now = utcnow()
    period_start = now
    period_end = now + timedelta(days=max(1, int(policy.billing_period_days)))

    trial_ends = None
    status = SubscriptionStatus.ACTIVE
    if start_trial:
        status = SubscriptionStatus.TRIAL
        trial_ends = now + timedelta(days=max(1, int(policy.trial_days)))

    sub = SuscripcionModulo(
        negocio_id=negocio_id,
        module_key=mk,
        status=status,
        started_at=now,
        trial_ends_at=trial_ends,
        current_period_start=period_start,
        current_period_end=period_end,
        next_renewal_at=period_end,
        cancel_at_period_end=0,
    )
    db.add(sub)
    db.flush()
    return sub


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
    # --- nuevo contrato (opcionales, no rompen callers)
    tenant_type: str = "customer",     # "customer" | "system"
    segment: str = "emprendedor",      # emprendedor|pyme|enterprise|superadmin
    # Provisioning:
    provision_inbound: bool = True,
    provision_wms: bool = False,
    start_trial: bool = True,
) -> Tuple[Negocio, Usuario]:
    """
    Crea un negocio y su usuario admin inicial, con provisioning SaaS “real”.

    Contrato operativo (baseline):
    - Tenant "customer": crea entitlements + suscripción INBOUND por defecto (trial) (WMS apagado)
    - Tenant "system": NO crea suscripciones comerciales (se usa para ORBION/superadmin si lo deseas)

    Auditoría (baseline v2.1):
    - NEGOCIO_STATE_UPDATE (creación -> activo)
    - MODULE_ACTIVATE (si se provisiona suscripción)
    - AUTH_LOGIN_OK / AUTH_LOGIN_FAIL con extra.event para signup
    """
    actor_user = {"email": email_admin}

    tt = (tenant_type or "customer").strip().lower()
    if tt not in {"customer", "system"}:
        tt = "customer"

    seg = _normalize_segment(segment)

    # Para system, forzamos segmento superadmin (si no viene)
    if tt == "system":
        seg = "superadmin"

    policy = ProvisioningPolicy(
        segment=seg,
        inbound_enabled=bool(provision_inbound) if tt == "customer" else False,
        wms_enabled=bool(provision_wms) if tt == "customer" else False,
    )

    try:
        # ----------------------------
        # Crear negocio (con entitlements coherentes al contrato)
        # ----------------------------
        negocio = Negocio(
            nombre_fantasia=nombre_negocio,
            whatsapp_notificaciones=whatsapp,
            estado=NegocioEstado.ACTIVO,
            plan_tipo="legacy",  # fallback interno
        )

        # Si tu modelo ya tiene tenant_type, lo seteamos sin romper si no existe
        if hasattr(negocio, "tenant_type"):
            setattr(negocio, "tenant_type", tt)

        # Set explícito de entitlements para evitar defaults viejos
        try:
            negocio.entitlements = _default_entitlements_for_policy(policy)
        except Exception:
            # si entitlements fuera Text JSON en algún entorno legacy
            pass

        db.add(negocio)
        db.flush()  # negocio.id

        # Auditoría: creación -> activo
        audit(
            db,
            action=AuditAction.NEGOCIO_STATE_UPDATE,
            user={"email": email_admin, "negocio_id": negocio.id, "rol": "admin"},
            negocio_id=negocio.id,
            entity_type="negocio",
            entity_id=negocio.id,
            before={"estado": None},
            after={"estado": "activo"},
            extra={
                "nombre_fantasia": nombre_negocio,
                "tenant_type": tt,
                "segment": seg,
            },
            commit=False,
        )

        # ----------------------------
        # Crear admin
        # ----------------------------
        usuario_admin = Usuario(
            negocio_id=negocio.id if tt == "customer" else None if getattr(Usuario, "negocio_id", None) is not None and tt == "system" else negocio.id,
            email=email_admin,
            password_hash=hash_password(password_admin),
            rol="admin" if tt == "customer" else "superadmin",
            activo=1,
            nombre_mostrado=nombre_admin,
        )
        db.add(usuario_admin)
        db.flush()

        audit(
            db,
            action=AuditAction.AUTH_LOGIN_OK,
            user={"email": email_admin, "negocio_id": negocio.id, "rol": usuario_admin.rol},
            negocio_id=negocio.id,
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
                    audit(
                        db,
                        action=AuditAction.MODULE_ACTIVATE,
                        user={"email": email_admin, "negocio_id": negocio.id, "rol": "admin"},
                        negocio_id=negocio.id,
                        entity_type="subscription",
                        entity_id=getattr(sub_inb, "id", None),
                        after={
                            "module": ModuleKey.INBOUND.value,
                            "status": getattr(sub_inb.status, "value", str(sub_inb.status)),
                            "trial": bool(start_trial),
                            "period_end": str(getattr(sub_inb, "current_period_end", None)) if getattr(sub_inb, "current_period_end", None) else None,
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
                    audit(
                        db,
                        action=AuditAction.MODULE_ACTIVATE,
                        user={"email": email_admin, "negocio_id": negocio.id, "rol": "admin"},
                        negocio_id=negocio.id,
                        entity_type="subscription",
                        entity_id=getattr(sub_wms, "id", None),
                        after={
                            "module": ModuleKey.WMS.value,
                            "status": getattr(sub_wms.status, "value", str(sub_wms.status)),
                            "trial": bool(start_trial),
                            "period_end": str(getattr(sub_wms, "current_period_end", None)) if getattr(sub_wms, "current_period_end", None) else None,
                        },
                        commit=False,
                    )

            logger.info(
                "[BUSINESS] provisioning negocio_id=%s created_subs=%s",
                negocio.id,
                [(s.module_key.value, getattr(s.status, "value", str(s.status))) for s in created_subs],
            )

        # “Signup ok” (canónica genérica)
        audit(
            db,
            action=AuditAction.AUTH_LOGIN_OK,
            user={"email": email_admin, "negocio_id": negocio.id, "rol": usuario_admin.rol},
            negocio_id=negocio.id if tt == "customer" else None,
            extra={"event": "auth.signup.ok", "tenant_type": tt},
            commit=False,
        )

        # Commit del flujo principal
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

        audit(
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

        logger.exception("[BUSINESS] crear_negocio_con_admin failed email=%s", email_admin)
        raise
