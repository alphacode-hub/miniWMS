# modules/inbound_orbion/routes/inbound_common.py
"""
Utilidades compartidas del módulo Inbound (ORBION).

✔ Resolver templates (global + inbound) sin hardcodes frágiles
✔ Dependency de roles inbound (RBAC)
✔ Helper seguro para obtener negocio
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.models import Negocio
from core.security import require_roles_dep
from core.web import templates
from core.web import add_template_dir  # ✅

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


def inbound_roles_dep() -> Callable:
    """
    Dependency para validar roles inbound.
    Uso:
        user = Depends(inbound_roles_dep())
    """
    return require_roles_dep(*INBOUND_ROLES)


# ============================
#   HELPERS
# ============================

def get_negocio_or_404(db: Session, negocio_id: int) -> Negocio:
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return negocio
