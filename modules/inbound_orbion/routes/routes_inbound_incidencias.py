# modules/inbound_orbion/routes/routes_inbound_incidencias.py
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
    obtener_recepcion_editable,
)

from modules.inbound_orbion.services.services_inbound_incidencias import (
    crear_incidencia,
    listar_incidencias_recepcion,
    obtener_incidencia,
    marcar_en_analisis,
    cerrar_incidencia,
    reabrir_incidencia,
    cancelar_incidencia,
    eliminar_incidencia_soft,
    obtener_resumen_incidencias,
    listar_fotos_incidencia,
    agregar_foto_incidencia_ref,
    eliminar_foto_soft,
)

from modules.inbound_orbion.services.services_inbound_lineas import (
    listar_lineas_recepcion,
    obtener_linea,
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
    if isinstance(user, dict):
        nid = user.get("negocio_id")
        if not nid:
            raise InboundDomainError("No se encontró negocio_id en la sesión.")
        return int(nid)

    nid = getattr(user, "negocio_id", None)
    if not nid:
        raise InboundDomainError("No se encontró negocio_id en la sesión.")
    return int(nid)


def _email_from_user(user) -> str | None:
    if isinstance(user, dict):
        return user.get("email")
    return getattr(user, "email", None)


def _to_str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        if not s.isdigit():
            raise InboundDomainError("Valor entero inválido.")
        return int(s)
    try:
        return int(v)
    except Exception as exc:
        raise InboundDomainError("Valor entero inválido.") from exc


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        s = s.replace(",", ".")
    else:
        s = v
    try:
        return float(s)
    except Exception as exc:
        raise InboundDomainError("Valor numérico inválido.") from exc


def _recepcion_editable_bool(recepcion) -> bool:
    # fallback robusto si el enum cambia
    try:
        est = recepcion.estado.name if recepcion.estado is not None else None
    except Exception:
        est = None
    return (est != "CERRADO")


def _resolve_linea_context(
    db: Session,
    *,
    negocio_id: int,
    recepcion_id: int,
    linea_id: int | None,
) -> tuple[int | None, str | None, str | None]:
    """
    Devuelve:
    - linea_id validada (o None)
    - unidad inferida desde línea/producto (o None)
    - lote sugerido desde línea (o None)
    """
    if not linea_id:
        return None, None, None

    linea = obtener_linea(db, negocio_id=negocio_id, linea_id=int(linea_id))
    if int(getattr(linea, "recepcion_id", 0)) != int(recepcion_id):
        raise InboundDomainError("La línea seleccionada no pertenece a esta recepción.")

    unidad = _to_str_or_none(getattr(linea, "unidad", None)) or _to_str_or_none(
        getattr(getattr(linea, "producto", None), "unidad", None)
    )
    lote = _to_str_or_none(getattr(linea, "lote", None))
    return int(linea.id), unidad, lote


# ============================================================
# LISTA
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

        # ✅ para el SELECT de productos: líneas reales de esta recepción
        lineas = listar_lineas_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        recepcion_editable = _recepcion_editable_bool(recepcion)

        return templates.TemplateResponse(
            "inbound_incidencias.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "incidencias": incidencias,
                "resumen": resumen,
                "lineas": lineas,
                "ok": ok,
                "error": error,
                "recepcion_editable": recepcion_editable,
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
                "lineas": [],
                "ok": None,
                "error": str(e),
                "recepcion_editable": True,
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
                "lineas": [],
                "ok": None,
                "error": "Error inesperado al abrir incidencias. Revisa logs.",
                "recepcion_editable": True,
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# CREAR
# ============================================================

@router.post("/recepciones/{recepcion_id}/incidencias/nueva", response_class=HTMLResponse)
async def inbound_incidencia_crear(
    request: Request,
    recepcion_id: int,
    tipo: str = Form(...),
    criticidad: str = Form(...),
    titulo: str | None = Form(None),
    detalle: str | None = Form(None),
    # ⛔ NO usamos pallet_id en UI (lo dejamos en DB por compat)
    pallet_id: int | None = Form(None),

    # ✅ NUEVO: vínculo a línea real + afectación
    linea_id: str | None = Form(None),
    cantidad_afectada: str | None = Form(None),
    lote: str | None = Form(None),

    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        # si está cerrada: bloquear
        obtener_recepcion_editable(db=db, recepcion_id=recepcion_id, negocio_id=negocio_id)

        linea_id_i = _to_int_or_none(linea_id)
        qty = _to_float_or_none(cantidad_afectada)

        resolved_linea_id, unidad_auto, lote_sugerido = _resolve_linea_context(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            linea_id=linea_id_i,
        )

        lote_final = _to_str_or_none(lote) or lote_sugerido
        unidad_final = unidad_auto  # no confiamos en input cliente

        crear_incidencia(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            tipo=_to_str_or_none(tipo) or "GENERAL",
            criticidad=_to_str_or_none(criticidad) or "MEDIA",
            titulo=_to_str_or_none(titulo),
            detalle=_to_str_or_none(detalle),
            pallet_id=None,  # ✅ forzamos no depender de pallets
            creado_por=email,

            # ✅ nuevos campos
            linea_id=resolved_linea_id,
            cantidad_afectada=qty,
            unidad=unidad_final,
            lote=lote_final,
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
# EDITAR (inline, desde lista)
# ============================================================

@router.post("/incidencias/{incidencia_id}/editar", response_class=HTMLResponse)
async def inbound_incidencia_editar(
    incidencia_id: int,
    recepcion_id: int = Form(...),

    tipo: str = Form(...),
    criticidad: str = Form(...),
    titulo: str | None = Form(None),
    detalle: str | None = Form(None),

    # ✅ nuevos campos
    linea_id: str | None = Form(None),
    cantidad_afectada: str | None = Form(None),
    lote: str | None = Form(None),

    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

        if int(getattr(inc, "recepcion_id", 0)) != int(recepcion_id):
            raise InboundDomainError("La incidencia no pertenece a esta recepción.")

        linea_id_i = _to_int_or_none(linea_id)
        qty = _to_float_or_none(cantidad_afectada)

        resolved_linea_id, unidad_auto, lote_sugerido = _resolve_linea_context(
            db,
            negocio_id=negocio_id,
            recepcion_id=int(recepcion_id),
            linea_id=linea_id_i,
        )

        inc.tipo = _to_str_or_none(tipo) or inc.tipo
        inc.criticidad = _to_str_or_none(criticidad) or inc.criticidad
        inc.titulo = _to_str_or_none(titulo)
        inc.detalle = _to_str_or_none(detalle)

        inc.linea_id = resolved_linea_id
        inc.cantidad_afectada = qty
        inc.unidad = unidad_auto  # forzado desde línea
        inc.lote = _to_str_or_none(lote) or lote_sugerido

        db.commit()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", ok="Incidencia actualizada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al editar incidencia. Revisa logs.",
        )


# ============================================================
# DETALLE
# ============================================================

@router.get("/incidencias/{incidencia_id}", response_class=HTMLResponse)
async def inbound_incidencia_detalle(
    request: Request,
    incidencia_id: int,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=int(recepcion_id))
        incidencia = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)
        fotos = listar_fotos_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)

        return templates.TemplateResponse(
            "inbound_incidencia_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "incidencia": incidencia,
                "fotos": fotos,
                "ok": ok,
                "error": error,
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_incidencia_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "incidencia": None,
                "fotos": [],
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=404,
        )

    except Exception:
        return templates.TemplateResponse(
            "inbound_incidencia_detalle.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "incidencia": None,
                "fotos": [],
                "ok": None,
                "error": "Error inesperado al abrir la incidencia. Revisa logs.",
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=500,
        )


# ============================================================
# ACCIONES ESTADO
# ============================================================

@router.post("/incidencias/{incidencia_id}/en-analisis", response_class=HTMLResponse)
async def inbound_incidencia_en_analisis(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        marcar_en_analisis(db, negocio_id=negocio_id, incidencia_id=incidencia_id)
        db.commit()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            ok="Incidencia en análisis.",
        )

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al actualizar estado. Revisa logs.",
        )


@router.post("/incidencias/{incidencia_id}/cerrar", response_class=HTMLResponse)
async def inbound_incidencia_cerrar(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    resolucion: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        cerrar_incidencia(
            db,
            negocio_id=negocio_id,
            incidencia_id=incidencia_id,
            resolucion=_to_str_or_none(resolucion),
            resuelto_por=email,
        )
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


@router.post("/incidencias/{incidencia_id}/reabrir", response_class=HTMLResponse)
async def inbound_incidencia_reabrir(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

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


@router.post("/incidencias/{incidencia_id}/cancelar", response_class=HTMLResponse)
async def inbound_incidencia_cancelar(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    motivo: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        cancelar_incidencia(
            db,
            negocio_id=negocio_id,
            incidencia_id=incidencia_id,
            motivo=_to_str_or_none(motivo),
            cancelado_por=email,
        )
        db.commit()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", ok="Incidencia cancelada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al cancelar incidencia. Revisa logs.",
        )


# ============================================================
# SOFT DELETE
# ============================================================

@router.post("/incidencias/{incidencia_id}/eliminar", response_class=HTMLResponse)
async def inbound_incidencia_eliminar(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        eliminar_incidencia_soft(db, negocio_id=negocio_id, incidencia_id=incidencia_id, eliminado_por=email)
        db.commit()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", ok="Incidencia eliminada (soft).")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/recepciones/{int(recepcion_id)}/incidencias", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/recepciones/{int(recepcion_id)}/incidencias",
            error="Error inesperado al eliminar incidencia. Revisa logs.",
        )


# ============================================================
# FOTOS (ref) - Agregar / Eliminar
# ✅ enterprise: también bloqueamos si recepción está cerrada
# ============================================================

@router.post("/incidencias/{incidencia_id}/fotos/agregar", response_class=HTMLResponse)
async def inbound_incidencia_foto_agregar(
    incidencia_id: int,
    recepcion_id: int = Form(...),
    titulo: str | None = Form(None),
    nota: str | None = Form(None),
    archivo_url: str | None = Form(None),
    archivo_path: str | None = Form(None),
    mime_type: str | None = Form(None),
    size_bytes: int | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        # ✅ no permitir si recepción cerrada
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        inc = obtener_incidencia(db, negocio_id=negocio_id, incidencia_id=incidencia_id)
        _ = agregar_foto_incidencia_ref(
            db,
            negocio_id=negocio_id,
            incidencia=inc,
            titulo=titulo,
            nota=nota,
            archivo_url=archivo_url,
            archivo_path=archivo_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            creado_por=email,
        )
        db.commit()
        return _redirect(f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}", ok="Foto agregada.")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}",
            error="Error inesperado al agregar foto. Revisa logs.",
        )


@router.post("/incidencias/{incidencia_id}/fotos/{foto_id}/eliminar", response_class=HTMLResponse)
async def inbound_incidencia_foto_eliminar(
    incidencia_id: int,
    foto_id: int,
    recepcion_id: int = Form(...),
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id_from_user(user)
    email = _email_from_user(user)

    try:
        # ✅ no permitir si recepción cerrada
        obtener_recepcion_editable(db=db, recepcion_id=int(recepcion_id), negocio_id=negocio_id)

        eliminar_foto_soft(db, negocio_id=negocio_id, foto_id=foto_id, eliminado_por=email)
        db.commit()
        return _redirect(f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}", ok="Foto eliminada (soft).")

    except InboundDomainError as e:
        db.rollback()
        return _redirect(f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}", error=str(e))

    except Exception:
        db.rollback()
        return _redirect(
            f"/inbound/incidencias/{incidencia_id}?recepcion_id={int(recepcion_id)}",
            error="Error inesperado al eliminar foto. Revisa logs.",
        )
