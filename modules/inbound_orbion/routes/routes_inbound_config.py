# modules/inbound_orbion/routes/routes_inbound_config.py

from fastapi import (
    APIRouter,
    Request,
    Depends,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import InboundConfig
from core.plans import get_inbound_plan_config
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
async def inbound_config_view(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]
    negocio = get_negocio_or_404(db, negocio_id)

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    if not config:
        config = InboundConfig(negocio_id=negocio_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    plan_cfg = get_inbound_plan_config(negocio.plan_tipo)

    log_inbound_event(
        "config_view",
        negocio_id=negocio_id,
        user_email=user["email"],
        plan=negocio.plan_tipo,
    )

    return templates.TemplateResponse(
        "inbound_config.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "config": config,
            "plan_cfg": plan_cfg,
            "modulo_nombre": "Orbion Inbound",
        },
    )


@router.post("/config", response_class=HTMLResponse)
async def inbound_config_save(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = user["negocio_id"]

    config = (
        db.query(InboundConfig)
        .filter(InboundConfig.negocio_id == negocio_id)
        .first()
    )

    if not config:
        config = InboundConfig(negocio_id=negocio_id)
        db.add(config)
        db.flush()

    form = await request.form()

    def parse_value(raw):
        if raw is None:
            return None
        raw = str(raw).strip()
        if raw == "":
            return None

        lower = raw.lower()
        if lower in ("true", "on", "yes", "si", "1"):
            return True
        if lower in ("false", "off", "no", "0"):
            return False

        if raw.isdigit():
            try:
                return int(raw)
            except ValueError:
                pass

        try:
            return float(raw)
        except ValueError:
            pass

        return raw

    for key, raw_value in form.items():
        if not hasattr(config, key):
            continue
        value = parse_value(raw_value)
        setattr(config, key, value)

    db.commit()
    db.refresh(config)

    log_inbound_event(
        "config_saved",
        negocio_id=negocio_id,
        user_email=user["email"],
    )

    return RedirectResponse(
        url="/inbound/config",
        status_code=302,
    )
