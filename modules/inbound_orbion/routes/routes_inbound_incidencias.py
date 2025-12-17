# modules/inbound_orbion/routes/routes_inbound_incidencias.py
from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)
from modules.inbound_orbion.services.services_inbound_incidencias import (
    crear_incidencia,
    listar_incidencias_recepcion,
    obtener_incidencia,
    cerrar_incidencia,
    reabrir_incidencia,
    obtener_resumen_incidencias,
)

from .inbound_common import templates, inbound_roles_dep

router = APIRouter()


# ============================================================
# Helpers
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
    # dict (baseline)
    if isinstance(user, dict):
        negocio_id = user.get("negocio_id")
        if not negocio_id:
            raise InboundDomainError("No se encontró negocio_id en la sesión.")
        return int(negocio_id)

    # objeto (por si algún día cambia tu auth)
    negocio_id = getattr(user, "negocio_id", None)
    if not negocio_id:
        raise InboundDomainError("No se encontró negocio_id en la sesión.")
    return int(negocio_id)


# ============================================================
# LISTA / DASHBOARD INCIDENCIAS POR RECEPCIÓN
# ============================================================

@router.get("/recepciones/{recepcion_id}/incidencias", response_class=HTMLResponse)
async def inbound_incidencias_recepcion(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        incidencias = listar_incidencias_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        resumen = obtener_resumen_incidencias(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        return templates.TemplateResponse(
            "inbound_incidencias.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "incidencias": incidencias,
                "resumen": resumen,
                "ok": ok,
                "error": error,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_incidencias.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "incidencias": [],
                "resumen": None,
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=404,
        )

    except Exception:
        return templates.TemplateResponse(
            "inbound_incidencias.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "incidencias": [],
                "resumen": None,
                "ok": None,
                "error": "Error inesperado al abrir incidencias. Revisa logs.",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# CREAR INCIDENCIA (POST)
# ============================================================

@router.post("/recepciones/{recepcion_id}/incidencias/nueva", response_class=HTMLResponse)
async def inbound_incidencia_crear(
    request: Request,
    recepcion_id: int,
    tipo: str = Form(...),
    criticidad: str = Form(...),
    titulo: str | None = Form(None),
    detalle: str | None = Form(None),
    pallet_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        crear_incidencia(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=tipo,
            criticidad=criticidad,
            titulo=titulo,
            detalle=detalle,
            pallet_id=pallet_id,
        )
        db.commit()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/incidencias", ok="Incidencia creada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{recepcion_id}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{recepcion_id}/incidencias",
            error="Error inesperado al crear incidencia. Revisa logs.",
        )


# ============================================================
# CERRAR INCIDENCIA (POST)
# ============================================================

@router.post("/incidencias/{incidencia_id}/cerrar", response_class=HTMLResponse)
async def inbound_incidencia_cerrar(
    request: Request,
    incidencia_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        cerrar_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)
        db.commit()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", ok="Incidencia cerrada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al cerrar incidencia. Revisa logs.",
        )


# ============================================================
# REABRIR INCIDENCIA (POST)
# ============================================================

@router.post("/incidencias/{incidencia_id}/reabrir", response_class=HTMLResponse)
async def inbound_incidencia_reabrir(
    request: Request,
    incidencia_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        reabrir_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)
        db.commit()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", ok="Incidencia reabierta.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al reabrir incidencia. Revisa logs.",
        )
