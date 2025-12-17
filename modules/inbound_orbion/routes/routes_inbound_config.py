# modules/inbound_orbion/routes/routes_inbound_config.py
"""
Rutas Config – Inbound ORBION

✔ Vista + guardado de configuración inbound por negocio
✔ Multi-tenant estricto
✔ Normalización robusta de form (bool/int/float/str)
✔ Logging estructurado
✔ Preparado para restricción a rol admin
"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundConfig
from core.plans import get_inbound_plan_config

from modules.inbound_orbion.services.services_inbound_config import (
    get_or_create_inbound_config,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()




# ============================
#   PARSE FORM (enterprise)
# ============================

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
        s2 = s.replace(",", ".")
        return float(s2)
    except ValueError:
        return s


def _require_admin_if_needed(user: dict) -> None:
    """
    Si decides restringir config solo a admin, activa esta validación.
    """
    # 🔒 Activar cuando quieras:
    # if user.get("rol") != "admin":
    #     raise HTTPException(
    #         status_code=403,
    #         detail="Solo el admin del negocio puede administrar la configuración inbound.",
    #     )
    return


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ============================
#   VIEW
# ============================

@router.get("/config", response_class=HTMLResponse)
async def inbound_config_view(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    _require_admin_if_needed(user)

    # ✅ centralizamos creación/lectura en service enterprise
    config = get_or_create_inbound_config(db, negocio_id)
    try:
        config_data = json.loads(config.reglas_json) if config.reglas_json else {}
    except Exception:
        config_data = {}

    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)

    log_inbound_event(
        "config_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        plan=negocio.plan_tipo,
    )

    return templates.TemplateResponse(
        "inbound_config.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "config": config,
            "config_data": config_data,
            "plan_cfg": plan_cfg,
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ============================
#   SAVE (enterprise robust)
# ============================

@router.post("/config", response_class=HTMLResponse)
async def inbound_config_save(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    get_negocio_or_404(db, negocio_id)

    _require_admin_if_needed(user)

    config = get_or_create_inbound_config(db, negocio_id)
    form = await request.form()

    # 1) Cargar JSON actual
    try:
        data: dict[str, Any] = json.loads(config.reglas_json) if config.reglas_json else {}
    except Exception:
        data = {}

    # 2) Aplicar valores del form con soporte para multi-values (checkbox + hidden)
    #    - Si llega ["false", "true"] -> debe quedar True
    #    - Si llega solo ["false"]    -> queda False
    #    - Si llega solo ["true"]     -> queda True
    #    - Si llega valor único       -> se parsea normal
    ignore_keys = {"csrf_token"}

    for key in form.keys():
        if key in ignore_keys:
            continue

        values = form.getlist(key)

        if not values:
            data[key] = None
            continue

        # Caso checkbox: prioriza true si aparece
        lowered = [str(v).strip().lower() for v in values if v is not None]
        if "true" in lowered or "on" in lowered or "1" in lowered or "yes" in lowered or "si" in lowered or "sí" in lowered:
            data[key] = True
            continue
        if "false" in lowered or "off" in lowered or "0" in lowered or "no" in lowered:
            # si solo vienen falsos, queda False
            data[key] = False
            continue

        # Fallback: toma el último valor y parsea
        data[key] = _parse_form_value(values[-1])

    # 3) Persistir
    config.reglas_json = json.dumps(data, ensure_ascii=False)
    config.updated_at = utcnow()

    db.commit()
    db.refresh(config)

    log_inbound_event(
        "config_saved",
        negocio_id=negocio_id,
        user_email=user.get("email"),
    )

    return RedirectResponse(url="/inbound/config", status_code=302)


