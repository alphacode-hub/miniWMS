# modules/inbound_orbion/routes/routes_inbound_citas.py
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.models.enums import CitaEstado

from modules.inbound_orbion.services.services_inbound_citas import (
    listar_citas,
    crear_cita,
    cambiar_estado_cita,
)
from modules.inbound_orbion.services.services_inbound_proveedores import (
    listar_proveedores,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
)

from .inbound_common import (
    templates,
    inbound_roles_dep,
    get_negocio_or_404,
)

router = APIRouter()


# ==========================================================
# HELPERS UX
# ==========================================================

def _qp(value: str | None) -> str:
    """Quote seguro para querystring."""
    return quote_plus((value or "").strip())


def _redirect(
    url: str,
    *,
    ok: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """
    Redirect estándar inbound con mensajes UX:
      - ?ok=...
      - ?error=...
    """
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"

    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"

    return RedirectResponse(url=url, status_code=302)


# ==========================================================
# LISTA / AGENDA DE CITAS
# ==========================================================

@router.get("/citas", response_class=HTMLResponse)
async def inbound_citas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    proveedores = listar_proveedores(
        db,
        negocio_id=negocio_id,
        solo_activos=True,
    )

    citas = listar_citas(
        db,
        negocio_id=negocio_id,
    )

    log_inbound_event(
        "citas_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total=len(citas),
    )

    return templates.TemplateResponse(
        "inbound_citas.html",
        {
            "request": request,
            "user": user,
            "citas": citas,
            "proveedores": proveedores,
            "estados": CitaEstado,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ==========================================================
# CREAR CITA
# ==========================================================

@router.post("/citas/nueva", response_class=HTMLResponse)
async def inbound_cita_crear(
    fecha_programada: str = Form(...),
    proveedor_id: int | None = Form(None),
    referencia: str = Form(""),
    notas: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])

    try:
        fecha = datetime.fromisoformat(fecha_programada)

        cita = crear_cita(
            db=db,
            negocio_id=negocio_id,
            fecha_programada=fecha,
            proveedor_id=proveedor_id,
            referencia=referencia,
            notas=notas,
        )

        log_inbound_event(
            "cita_creada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita.id,
        )

        return _redirect(
            "/inbound/citas",
            ok="Cita creada correctamente.",
        )

    except (ValueError, InboundDomainError) as exc:
        log_inbound_error(
            "cita_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(exc),
        )
        return _redirect(
            "/inbound/citas",
            error=str(exc),
        )


# ==========================================================
# CAMBIAR ESTADO DE CITA
# ==========================================================

@router.post("/citas/{cita_id}/estado", response_class=HTMLResponse)
async def inbound_cita_estado(
    cita_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])

    try:
        nuevo_estado = CitaEstado[estado]

        cita = cambiar_estado_cita(
            db=db,
            negocio_id=negocio_id,
            cita_id=cita_id,
            nuevo_estado=nuevo_estado,
        )

        log_inbound_event(
            "cita_estado_actualizado",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita.id,
            estado=cita.estado.value,
        )

        return _redirect(
            "/inbound/citas",
            ok="Estado de cita actualizado.",
        )

    except (KeyError, InboundDomainError) as exc:
        log_inbound_error(
            "cita_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
            error=str(exc),
        )
        return _redirect(
            "/inbound/citas",
            error="Estado de cita inválido.",
        )
