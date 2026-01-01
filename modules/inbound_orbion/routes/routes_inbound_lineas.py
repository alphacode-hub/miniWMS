# modules/inbound_orbion/routes/routes_inbound_lineas.py
from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
)

from modules.inbound_orbion.services.services_inbound_lineas import (
    listar_lineas_recepcion,
)

from modules.inbound_orbion.services.services_inbound_incidencias import (
    obtener_resumen_incidencias_cuantitativo,
)

from .inbound_common import templates, inbound_roles_dep


router = APIRouter()


# ============================================================
# Helpers (baseline aligned)
# ============================================================

def _qp(msg: str) -> str:
    return quote_plus((msg or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _negocio_id_from_user(user) -> int:
    """
    Compatible con user dict (session) o modelo Usuario.
    """
    if isinstance(user, dict):
        nid = user.get("negocio_id")
        if not nid:
            raise InboundDomainError("No se encontró negocio_id en la sesión.")
        return int(nid)

    nid = getattr(user, "negocio_id", None)
    if not nid:
        raise InboundDomainError("No se encontró negocio_id en la sesión.")
    return int(nid)


# ============================================================
# LISTA
# ============================================================

@router.get("/recepciones/{recepcion_id}/lineas", response_class=HTMLResponse)
async def inbound_lineas_lista(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    # compat qs
    qs_success = request.query_params.get("success") or request.query_params.get("ok")
    qs_error = request.query_params.get("error")

    # show recon
    show_recon = (request.query_params.get("recon") == "1")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # si está cerrada, obtener_recepcion_editable lanzará error si intentas modificar,
        # pero acá solo queremos un bool para UI (por eso lo atrapamos)
        try:
            _ = obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)
            recepcion_editable = True
        except Exception:
            recepcion_editable = False

        lineas = listar_lineas_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        # ✅ incidencias: resumen cuantitativo para "explicado por incidencias"
        inc_resumen = obtener_resumen_incidencias_cuantitativo(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            include_cerradas=True,
            exclude_canceladas=True,
        )

        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "lineas": lineas,
                "recepcion_editable": recepcion_editable,
                "qs_success": qs_success,
                "qs_error": qs_error,
                "show_recon": show_recon,
                "inc_resumen": inc_resumen,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "lineas": [],
                "recepcion_editable": True,
                "qs_success": None,
                "qs_error": str(e),
                "show_recon": show_recon,
                "inc_resumen": {"totales": {"count": 0, "qty": 0.0, "kg": 0.0}, "por_linea": {}},
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=404,
        )

    except Exception:
        return templates.TemplateResponse(
            "inbound_lineas_lista.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "lineas": [],
                "recepcion_editable": True,
                "qs_success": None,
                "qs_error": "Error inesperado al abrir líneas. Revisa logs.",
                "show_recon": show_recon,
                "inc_resumen": {"totales": {"count": 0, "qty": 0.0, "kg": 0.0}, "por_linea": {}},
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )
