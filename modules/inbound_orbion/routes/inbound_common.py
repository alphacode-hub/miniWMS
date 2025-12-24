# modules/inbound_orbion/routes/inbound_common.py
"""
Utilidades compartidas del módulo Inbound (ORBION).

✔ Resolver templates (global + inbound) sin hardcodes frágiles
✔ Dependency de roles inbound (RBAC) + gating de módulo (entitlements + subscription overlay)
✔ Helper seguro para obtener negocio
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import Negocio
from core.security import require_roles_dep, require_user_dep
from core.services.services_entitlements import has_module_db
from core.web import add_template_dir, templates

# ============================
#   TEMPLATES (registrar dir inbound)
# ============================

_THIS_FILE = Path(__file__).resolve()
# <root>/modules/inbound_orbion/routes/inbound_common.py
_PROJECT_ROOT = _THIS_FILE.parents[3]
_INBOUND_TEMPLATES_DIR = _THIS_FILE.parents[1] / "templates"  # modules/inbound_orbion/templates

# ✅ Se registra una sola vez en el loader global
add_template_dir(_INBOUND_TEMPLATES_DIR)

# ============================
#   ROLES PERMITIDOS (INBOUND)
# ============================

INBOUND_ROLES: Tuple[str, ...] = (
    "admin",
    "operador",
    "operador_inbound",
    "supervisor_inbound",
    "auditor_inbound",
    "transportista",
)


def _effective_negocio_id(user: dict) -> int | None:
    """
    Baseline:
    - acting_negocio_id (impersonación) tiene prioridad
    - si no, negocio_id normal
    """
    try:
        if user.get("acting_negocio_id"):
            return int(user["acting_negocio_id"])
    except Exception:
        pass

    try:
        if user.get("negocio_id"):
            return int(user["negocio_id"])
    except Exception:
        pass

    return None


def inbound_roles_dep() -> Callable:
    """
    Dependency inbound enterprise (RBAC + gating):
    - valida sesión usuario
    - valida roles inbound
    - valida que el módulo inbound esté ACTIVO (effective enabled) según snapshot
      (entitlements.enabled + subscription.status ∈ {trial, active})

    Uso:
        user = Depends(inbound_roles_dep())
    """

    roles_dep = require_roles_dep(*INBOUND_ROLES)

    async def _dep(
        request: Request,
        db: Session = Depends(get_db),
        user: dict = Depends(require_user_dep),
    ) -> dict:
        # 1) roles inbound
        roles_dep(user)  # lanza 403 si no tiene roles

        # 2) gating módulo (enterprise)
        negocio_id = _effective_negocio_id(user)
        if not negocio_id:
            raise HTTPException(status_code=400, detail="No se encontró contexto de negocio en la sesión.")

        if not has_module_db(db, negocio_id, "inbound", require_active=True):
            # mensaje claro para UI
            raise HTTPException(
                status_code=403,
                detail="Módulo Inbound no está activo para este negocio (suscripción suspendida o no habilitada).",
            )

        return user

    return _dep


# ============================
#   HELPERS
# ============================

def get_negocio_or_404(db: Session, negocio_id: int) -> Negocio:
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return negocio
