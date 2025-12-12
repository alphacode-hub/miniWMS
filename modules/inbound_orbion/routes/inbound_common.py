# modules/inbound_orbion/routes/inbound_common.py

from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.models import Negocio
from core.security import require_roles_dep

# ============================
#   TEMPLATES COMPARTIDOS
# ============================

HERE = Path(__file__).resolve()

PROJECT_ROOT = HERE.parents[3]   # .../miniWMS
INBOUND_TEMPLATES = HERE.parents[1] / "templates"  # modules/inbound_orbion/templates
GLOBAL_TEMPLATES = PROJECT_ROOT / "templates"      # templates/

templates = Jinja2Templates(
    directory=[
        str(GLOBAL_TEMPLATES),       # base/base_app.html y otros html globales
        str(INBOUND_TEMPLATES),      # inbound_lista.html, inbound_detalle.html, etc.
    ]
)

# ============================
#   ROLES PERMITIDOS INBOUND
# ============================

INBOUND_ROLES = (
    "admin",
    "operador",
    "operador_inbound",
    "supervisor_inbound",
    "auditor_inbound",
    "transportista",
)

def inbound_roles_dep():
    """
    Devuelve el dependency para validar roles inbound.
    Uso: user = Depends(inbound_roles_dep())
    """
    return require_roles_dep(*INBOUND_ROLES)


# ============================
#   HELPERS DE NEGOCIO
# ============================

def get_negocio_or_404(db: Session, negocio_id: int) -> Negocio:
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return negocio
