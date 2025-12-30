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
    crear_cita_y_recepcion,
    cambiar_estado_cita,
    cancelar_cita_y_recepcion,
)
from modules.inbound_orbion.services.services_inbound_proveedores import (
    listar_proveedores,
    listar_plantillas_activas_grouped_por_proveedor,
)
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_core import InboundDomainError

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# ==========================================================
# Helpers UX
# ==========================================================

def _qp(value: str | None) -> str:
    return quote_plus((value or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _parse_datetime_local(value: str) -> datetime:
    """
    datetime-local HTML => "YYYY-MM-DDTHH:MM" (sin zona horaria).
    Por ahora lo dejamos naive, pero si ya tienes helper _ensure_utc_aware en core,
    lo ideal es convertir aquí antes de persistir.
    """
    return datetime.fromisoformat(value)


# ==========================================================
# Lista / Agenda
# ==========================================================

@router.get("/citas", response_class=HTMLResponse)
async def inbound_citas_lista(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])
    get_negocio_or_404(db, negocio_id)

    proveedores = listar_proveedores(db, negocio_id=negocio_id, solo_activos=True)

    # ✅ 1 query, agrupado (evita N+1)
    plantillas_por_proveedor = listar_plantillas_activas_grouped_por_proveedor(db, negocio_id)

    citas = listar_citas(db, negocio_id=negocio_id)

    log_inbound_event(
        "citas_lista_view",
        negocio_id=negocio_id,
        user_email=user.get("email"),
        total=len(citas or []),
    )

    return templates.TemplateResponse(
        "inbound_citas.html",
        {
            "request": request,
            "user": user,
            "citas": citas,
            "proveedores": proveedores,
            "plantillas_por_proveedor": plantillas_por_proveedor,
            "estados": CitaEstado,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
            "modulo_nombre": "Orbion Inbound",
        },
    )


# ==========================================================
# Crear Cita + Recepción 1:1 (Cita manda)
# ==========================================================

@router.post("/citas/nueva", response_class=HTMLResponse)
async def inbound_cita_crear(
    fecha_programada: str = Form(...),
    proveedor_id: int | None = Form(None),
    plantilla_id: int | None = Form(None),
    referencia: str = Form(""),
    notas: str = Form(""),

    # ✅ nuevos
    contenedor: str = Form(""),
    patente_camion: str = Form(""),
    tipo_carga: str = Form(""),

    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])

    try:
        fecha = datetime.fromisoformat(fecha_programada)

        cita, recepcion = crear_cita_y_recepcion(
            db=db,
            negocio_id=negocio_id,
            fecha_programada=fecha,
            proveedor_id=proveedor_id,
            referencia=referencia,
            notas=notas,
            plantilla_id=plantilla_id,

            # ✅ pasar transporte
            contenedor=contenedor,
            patente_camion=patente_camion,
            tipo_carga=tipo_carga,
        )

        log_inbound_event(
            "cita_y_recepcion_creadas",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita.id,
            recepcion_id=recepcion.id,
            proveedor_id=proveedor_id,
            plantilla_id=plantilla_id,
        )

        return _redirect(
            "/inbound/citas",
            ok=f"Cita creada y recepción {recepcion.codigo_recepcion} pre-registrada.",
        )

    except (ValueError, InboundDomainError) as exc:
        log_inbound_error(
            "cita_crear_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(exc),
        )
        return _redirect("/inbound/citas", error=str(exc))


# ==========================================================
# Cambiar estado (manual)
# - NO usar esto para CANCELAR (usa endpoint /cancelar)
# ==========================================================

@router.post("/citas/{cita_id}/estado")
async def inbound_cita_estado(
    cita_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])

    try:
        nuevo_estado = CitaEstado[estado]

        if nuevo_estado == CitaEstado.CANCELADA:
            return _redirect("/inbound/citas", error="Para cancelar usa el botón Cancelar (cita manda).")

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

        return _redirect("/inbound/citas", ok="Estado de cita actualizado.")

    except KeyError:
        log_inbound_error(
            "cita_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
            error="estado_invalido",
        )
        return _redirect("/inbound/citas", error="Estado de cita inválido.")
    except InboundDomainError as exc:
        log_inbound_error(
            "cita_estado_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
            error=str(exc),
        )
        return _redirect("/inbound/citas", error=str(exc))


# ==========================================================
# Cancelar (Cita manda => cancela Recepción 1:1)
# ==========================================================

@router.post("/citas/{cita_id}/cancelar")
async def inbound_cita_cancelar(
    cita_id: int,
    motivo: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = int(user["negocio_id"])

    try:
        cancelar_cita_y_recepcion(
            db,
            negocio_id=negocio_id,
            cita_id=cita_id,
            motivo=motivo,
        )

        log_inbound_event(
            "cita_cancelada",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
        )

        return _redirect("/inbound/citas", ok="Cita cancelada (y recepción asociada cancelada si existía).")

    except InboundDomainError as exc:
        log_inbound_error(
            "cita_cancelar_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            cita_id=cita_id,
            error=str(exc),
        )
        return _redirect("/inbound/citas", error=str(exc))
