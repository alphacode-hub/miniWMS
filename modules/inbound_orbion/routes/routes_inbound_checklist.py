# modules/inbound_orbion/routes/routes_inbound_checklist.py
from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

from modules.inbound_orbion.services.services_inbound_checklist import (
    obtener_checklist_vm,
    guardar_respuesta_item,
)

from .inbound_common import inbound_roles_dep, templates

router = APIRouter()


# =========================================================
# Helpers sesión / identidad (baseline: user es dict)
# =========================================================

def _get_negocio_id(user: dict) -> int:
    val = user.get("negocio_id")
    if val is None:
        raise InboundDomainError("Sesión inválida: falta negocio_id.")
    return int(val)


def _get_user_identity(user: dict) -> str | None:
    return user.get("email") or user.get("usuario") or user.get("nombre") or None


def _redirect_back(
    request: Request,
    recepcion_id: int,
    *,
    ok: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    url = str(request.url_for("inbound_checklist_recepcion", recepcion_id=recepcion_id))
    if ok:
        url += f"?ok={quote_plus(ok)}"
    elif error:
        url += f"?error={quote_plus(error)}"
    return RedirectResponse(url=url, status_code=303)


def _is_locked(recepcion, checklist_vm) -> tuple[bool, str]:
    """
    Solo lectura si:
    - Recepción CERRADO
    - Checklist bloqueado por críticos NO_CUMPLE (regla de negocio, calculada en resumen)
    """
    recep_estado = (recepcion.estado.value if recepcion and recepcion.estado else "")
    if recep_estado == "CERRADO":
        return True, "Recepción cerrada: checklist en solo lectura."

    # SIMPLE V2: no existe estado/firma en cabecera.
    # Bloqueo se calcula en el VM (resumen["bloqueado"]).
    try:
        resumen = getattr(checklist_vm, "resumen", None) or {}
        if bool(resumen.get("bloqueado")):
            return True, "Checklist bloqueado por ítems críticos NO CUMPLE."
    except Exception:
        pass

    return False, ""


# =========================================================
# CHECKLIST – VISTA
# =========================================================

@router.get(
    "/recepciones/{recepcion_id}/checklist",
    response_class=HTMLResponse,
    name="inbound_checklist_recepcion",
)
async def inbound_checklist_recepcion(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    qs_ok = request.query_params.get("ok")
    qs_error = request.query_params.get("error")

    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        vm = obtener_checklist_vm(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        locked, why = _is_locked(recepcion, vm)

        return templates.TemplateResponse(
            "inbound_checklist_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "checklist": vm,
                "locked": locked,
                "locked_reason": why,
                "ok": qs_ok,
                "error": qs_error,
                "qs_ok": qs_ok,
                "qs_error": qs_error,
            },
        )

    except InboundDomainError as e:
        msg = getattr(e, "message", None) or str(e)
        return templates.TemplateResponse(
            "inbound_checklist_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "checklist": None,
                "locked": True,
                "locked_reason": msg,
                "ok": None,
                "error": msg,
                "qs_ok": None,
                "qs_error": msg,
            },
            status_code=200,
        )


# =========================================================
# VM JSON – para refrescar UI sin reload
# =========================================================

@router.get("/recepciones/{recepcion_id}/checklist/vm")
async def inbound_checklist_vm_json(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        vm = obtener_checklist_vm(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        locked, why = _is_locked(recepcion, vm)

        # vm es dataclass: __dict__ sirve OK para payload plano
        return JSONResponse(
            {
                "ok": True,
                "locked": locked,
                "locked_reason": why,
                "resumen": vm.resumen,
                "items": [i.__dict__ for i in vm.items],
                "secciones": [
                    {
                        "seccion_id": s.seccion_id,
                        "codigo": s.codigo,
                        "titulo": s.titulo,
                        "orden": s.orden,
                        "resumen": s.resumen,
                        "items": [i.__dict__ for i in s.items],
                    }
                    for s in vm.secciones
                ],
                "plantilla_id": vm.plantilla_id,
                "plantilla_nombre": vm.plantilla_nombre,
                "ejecucion_id": vm.ejecucion_id,
                "actualizado_en": (vm.actualizado_en.isoformat() if vm.actualizado_en else None),
            }
        )

    except InboundDomainError as e:
        msg = getattr(e, "message", None) or str(e)
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# =========================================================
# AUTOSAVE – UPSERT (AJAX)
# =========================================================

@router.post("/recepciones/{recepcion_id}/checklist/autosave")
async def inbound_checklist_autosave(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    """
    Autosave SIMPLE V2 (JSON)

    payload:
      {
        "item_id": 123,
        "estado": "PENDIENTE|CUMPLE|NO_CUMPLE|NA",
        "nota": "..." (opcional)
      }

    response:
      { ok, resumen, item_updated, server_updated_at }
    """
    negocio_id = _get_negocio_id(user)
    respondido_por = _get_user_identity(user)

    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        vm = obtener_checklist_vm(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        locked, why = _is_locked(recepcion, vm)
        if locked:
            raise InboundDomainError(why)

        payload = await request.json()

        # Compat: aceptamos item_id (nuevo) o checklist_item_id (legacy)
        raw_item_id = payload.get("item_id", None)
        if raw_item_id is None:
            raw_item_id = payload.get("checklist_item_id", None)

        if raw_item_id is None:
            raise InboundDomainError("Falta item_id en payload.")

        item_id = int(raw_item_id)

        estado = payload.get("estado", None)
        # Compat legacy: venía como 'valor'
        if estado is None:
            estado = payload.get("valor", None)

        nota = payload.get("nota", None)

        guardar_respuesta_item(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            item_id=item_id,
            estado=(str(estado) if estado is not None else None),
            nota=(str(nota) if nota is not None else None),
            respondido_por=respondido_por,
        )
        db.commit()

        vm2 = obtener_checklist_vm(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        item_updated = None
        for it in vm2.items:
            if it.item_id == item_id:
                item_updated = it.__dict__
                break

        return JSONResponse(
            {
                "ok": True,
                "locked": _is_locked(recepcion, vm2)[0],
                "resumen": vm2.resumen,
                "item_updated": item_updated,
                "server_updated_at": (vm2.actualizado_en.isoformat() if vm2.actualizado_en else None),
            }
        )

    except InboundDomainError as e:
        db.rollback()
        msg = getattr(e, "message", None) or str(e)
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    except Exception as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
