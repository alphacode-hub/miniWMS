# modules/inbound_orbion/routes/routes_inbound_checklist.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

from modules.inbound_orbion.services.services_inbound_checklist import (
    obtener_checklist_vm,
    guardar_respuesta_item,
    completar_checklist_recepcion,
    reabrir_checklist_recepcion,
)

from .inbound_common import templates, inbound_roles_dep

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


def _redirect_back(request: Request, recepcion_id: int) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url_for("inbound_checklist_recepcion", recepcion_id=recepcion_id)),
        status_code=303,
    )


def _parse_ok_form(ok: str | None) -> bool | None:
    """
    Tri-state BOOL:
    - "true"  => True
    - "false" => False
    - ""/None => None (pendiente)
    """
    if ok is None:
        return None
    s = str(ok).strip().lower()
    if s in ("true", "1", "yes", "si", "sí", "ok"):
        return True
    if s in ("false", "0", "no", "nok", "no_ok"):
        return False
    return None


# =========================================================
# CHECKLIST – VISTA
# =========================================================

@router.get("/recepciones/{recepcion_id}/checklist", response_class=HTMLResponse)
async def inbound_checklist_recepcion(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        recepcion = obtener_recepcion_segura(db, recepcion_id=recepcion_id, negocio_id=negocio_id)
        vm = obtener_checklist_vm(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

        return templates.TemplateResponse(
            "inbound_checklist_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": recepcion,
                "checklist": vm,
                "error": request.query_params.get("error"),
                "ok": request.query_params.get("ok"),
            },
        )

    except InboundDomainError as e:
        return templates.TemplateResponse(
            "inbound_checklist_recepcion.html",
            {
                "request": request,
                "user": user,
                "recepcion": None,
                "checklist": None,
                "error": getattr(e, "message", None) or str(e),
                "ok": None,
            },
            status_code=200,
        )


# =========================================================
# CHECKLIST – GUARDAR ÍTEM (POST)
# =========================================================

@router.post("/recepciones/{recepcion_id}/checklist/item", response_class=HTMLResponse)
async def inbound_checklist_guardar_item(
    request: Request,
    recepcion_id: int,
    checklist_item_id: int = Form(...),

    # Tri-state radio: "true" / "false" / "" (pendiente)
    ok: str | None = Form(None),
    valor: str | None = Form(None),
    nota: str | None = Form(None),

    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)
    respondido_por = _get_user_identity(user)

    ok_bool = _parse_ok_form(ok)

    try:
        guardar_respuesta_item(
            db,
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            checklist_item_id=int(checklist_item_id),
            ok=ok_bool,
            valor=valor,
            nota=nota,
            respondido_por=respondido_por,
        )
        db.commit()
        return _redirect_back(request, recepcion_id)

    except InboundDomainError as e:
        db.rollback()
        return RedirectResponse(
            url=str(request.url_for("inbound_checklist_recepcion", recepcion_id=recepcion_id))
            + f"?error={str(getattr(e,'message',None) or e)}",
            status_code=303,
        )


# =========================================================
# CHECKLIST – GUARDAR TODO (AJAX)
# =========================================================

@router.post("/recepciones/{recepcion_id}/checklist/guardar_todo")
async def inbound_checklist_guardar_todo(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    """
    Guardar todo (modo seguro):
    - BOOL: guarda SOLO los que están OK (ok=True)
      (NO OK o Pendiente => no se guarda en bulk)
    - NO BOOL: guarda solo si viene valor o nota no vacíos
    """
    negocio_id = _get_negocio_id(user)
    respondido_por = _get_user_identity(user)

    try:
        payload = await request.json()
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise InboundDomainError("Payload inválido (items).")

        guardados = 0

        for row in items:
            cid = int(row.get("checklist_item_id"))
            tipo = (row.get("tipo") or "").strip().upper()

            ok = row.get("ok", None)
            valor = row.get("valor", None)
            nota = row.get("nota", None)

            v = (valor or "").strip() if isinstance(valor, str) else ""
            n = (nota or "").strip() if isinstance(nota, str) else ""

            # --- MODO SEGURO ---
            if tipo == "BOOL":
                # solo guardamos si viene ok=True (OK)
                if ok is not True:
                    continue
                ok_bool = True
                valor_to_save = None
            else:
                # no-bool: guardamos solo si hay valor o nota
                if not v and not n:
                    continue
                ok_bool = None
                valor_to_save = v or None

            guardar_respuesta_item(
                db,
                negocio_id=negocio_id,
                recepcion_id=recepcion_id,
                checklist_item_id=cid,
                ok=ok_bool,
                valor=valor_to_save,
                nota=n or None,
                respondido_por=respondido_por,
            )
            guardados += 1

        db.commit()
        return JSONResponse({"ok": True, "guardados": guardados})

    except InboundDomainError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": getattr(e, "message", None) or str(e)}, status_code=400)
    except Exception as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# =========================================================
# CHECKLIST – COMPLETAR / REABRIR
# =========================================================

@router.post("/recepciones/{recepcion_id}/checklist/completar", response_class=HTMLResponse)
async def inbound_checklist_completar(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        completar_checklist_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        db.commit()
        return _redirect_back(request, recepcion_id)

    except InboundDomainError as e:
        db.rollback()
        return RedirectResponse(
            url=str(request.url_for("inbound_checklist_recepcion", recepcion_id=recepcion_id))
            + f"?error={str(getattr(e,'message',None) or e)}",
            status_code=303,
        )


@router.post("/recepciones/{recepcion_id}/checklist/reabrir", response_class=HTMLResponse)
async def inbound_checklist_reabrir(
    request: Request,
    recepcion_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(inbound_roles_dep()),
):
    negocio_id = _get_negocio_id(user)

    try:
        reabrir_checklist_recepcion(db, negocio_id=negocio_id, recepcion_id=recepcion_id)
        db.commit()
        return _redirect_back(request, recepcion_id)

    except InboundDomainError as e:
        db.rollback()
        return RedirectResponse(
            url=str(request.url_for("inbound_checklist_recepcion", recepcion_id=recepcion_id))
            + f"?error={str(getattr(e,'message',None) or e)}",
            status_code=303,
        )
