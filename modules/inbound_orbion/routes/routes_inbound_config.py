# modules/inbound_orbion/routes/routes_inbound_config.py
"""
Rutas Config – Inbound ORBION (baseline entitlements)

✔ Vista + guardado de configuración inbound por negocio
✔ Multi-tenant estricto
✔ Parse robusto de form (bool/int/float/str) con hidden+checkbox
✔ Logging estructurado
✔ Plan summary vía ENTITLEMENTS (NO plan_tipo legacy)
✔ SLA en MINUTOS como ENTEROS (baseline)
"""

from __future__ import annotations

from typing import Any
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.time import utcnow  # ✅ baseline utcnow

from modules.inbound_orbion.services.services_inbound_config import (
    get_or_create_inbound_config,
    normalize_inbound_config_dict,
    get_inbound_config_page_context,
)

from modules.inbound_orbion.services.services_inbound_logging import log_inbound_event
from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()

_TRUE_SET = {"true", "on", "yes", "si", "sí", "1"}
_FALSE_SET = {"false", "off", "no", "0"}


def _parse_form_value(raw: Any):
    """
    Convierte un input de form en:
      - None si vacío
      - bool si parece checkbox/switch
      - int si calza perfecto
      - float (tolera coma) si calza
      - str fallback
    """
    if raw is None:
        return None

    s = str(raw).strip()
    if s == "":
        return None

    lower = s.lower()
    if lower in _TRUE_SET:
        return True
    if lower in _FALSE_SET:
        return False

    # int (solo si es entero puro)
    try:
        if "." not in s and "," not in s:
            return int(s)
    except ValueError:
        pass

    # float (tolera coma decimal)
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return s


def _require_admin_if_needed(user: dict) -> None:
    """
    Si decides restringir config solo a admin, activa esta validación.
    """
    # 🔒 Activar cuando quieras:
    # if (user.get("rol") or "").strip().lower() != "admin":
    #     raise HTTPException(status_code=403, detail="Solo admin puede administrar configuración inbound.")
    return


# ============================
#   VIEW
# ============================

@router.get("/config", response_class=HTMLResponse)
async def inbound_config_view(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    negocio = get_negocio_or_404(db, negocio_id)

    _require_admin_if_needed(user)

    # ✅ Ruta liviana: todo el contexto sale del service (config/config_data normalizado/plan_cfg entitlements)
    ctx = get_inbound_config_page_context(db, negocio)

    log_inbound_event(
        "config_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
    )

    return templates.TemplateResponse(
        "inbound_config.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            **ctx,  # {"config":..., "config_data":..., "plan_cfg":...}
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   SAVE (enterprise robust)
# ============================

@router.post("/config")
async def inbound_config_save(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    _require_admin_if_needed(user)

    config = get_or_create_inbound_config(db, negocio_id)
    form = await request.form()

    # 1) Cargar JSON actual
    try:
        data: dict[str, Any] = json.loads(config.reglas_json) if config.reglas_json else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # 2) Aplicar valores del form con soporte multi-values (checkbox + hidden)
    ignore_keys = {"csrf_token"}

    for key in form.keys():
        if key in ignore_keys:
            continue

        values = form.getlist(key)
        if not values:
            data[key] = None
            continue

        lowered = [str(v).strip().lower() for v in values if v is not None]

        # checkbox con hidden: prioriza True si aparece
        if any(v in _TRUE_SET for v in lowered):
            data[key] = True
            continue
        if any(v in _FALSE_SET for v in lowered):
            data[key] = False
            continue

        # Fallback: toma el último valor y parsea
        data[key] = _parse_form_value(values[-1])

    # ✅ 3) Normalizar baseline (SLA ENTEROS, bools, ints) antes de guardar
    data = normalize_inbound_config_dict(data)

    # 4) Persistir
    config.reglas_json = json.dumps(data, ensure_ascii=False)
    config.updated_at = utcnow()

    db.commit()
    db.refresh(config)

    log_inbound_event(
        "config_saved",
        negocio_id=negocio_id,
        user_email=user.get("email"),
    )

    return RedirectResponse(url="/inbound/config?success=1", status_code=302)
