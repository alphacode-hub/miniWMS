# core/routes/routes_auth.py
"""
Auth & Session routes – ORBION (SaaS enterprise, baseline aligned)

✔ Login centralizado en /app/login
✔ Logout seguro (revoca sesión en BD)
✔ Redirección única post-login (/app)
✔ Manejo robusto de cookies firmadas
✔ Templates SSR (Jinja2)

Baseline audit v2.1:
- Auditoría ES por negocio (tenant real).
- Si NO hay negocio_id resoluble (ej: email inexistente / superadmin global),
  NO se audita (se registra solo en logs/observability).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from itsdangerous import BadSignature

from core.web import templates
from core.config import settings
from core.database import get_db
from core.models import Usuario, SesionUsuario
from core.models.enums import NegocioEstado
from core.security import (
    get_current_user,
    verify_password,
    crear_sesion_db,
    crear_cookie_sesion,
    signer,
    SESSION_INACTIVITY_SECONDS,
)
from core.logging_config import logger
from core.services.services_audit import audit, AuditAction, build_request_ctx


router = APIRouter(prefix="/app", tags=["auth"])


# =========================================================
# HELPERS
# =========================================================

def _audit_user_dict(*, email: str, rol: str, negocio_id: int | None = None) -> dict:
    """
    Formato mínimo que audit v2.1 entiende (resolve negocio_id desde user dict).
    """
    return {"email": email, "rol": rol, "negocio_id": negocio_id}


def _audit_user_from_current(current: dict | None) -> dict:
    """
    Normaliza current_user -> dict usable por audit().
    """
    cur = current or {}
    return {
        "email": cur.get("email") or cur.get("usuario") or "sistema",
        "rol": cur.get("rol_real") or cur.get("rol") or "unknown",
        "rol_real": cur.get("rol_real") or cur.get("rol"),
        "negocio_id": cur.get("negocio_id"),
        "acting_negocio_id": cur.get("acting_negocio_id"),
        "impersonando_negocio_id": cur.get("impersonando_negocio_id"),
    }


def _resolve_audit_negocio_id(audit_user: dict) -> int | None:
    """
    Baseline: audit pertenece al negocio del contexto efectivo.
    Si hay acting_negocio_id (impersonación), ese manda.
    """
    try:
        acting = audit_user.get("acting_negocio_id")
        if acting:
            return int(acting)
    except Exception:
        pass

    try:
        nid = audit_user.get("negocio_id")
        return int(nid) if nid else None
    except Exception:
        return None


def _delete_session_cookie(resp: RedirectResponse) -> None:
    resp.delete_cookie(key=settings.SESSION_COOKIE_NAME, path="/")


# =========================================================
# LOGIN
# =========================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/app", status_code=302)

    return templates.TemplateResponse(
        "app/login.html",
        {"request": request, "error": None, "user": None, "email": ""},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = (email or "").strip().lower()
    request_ctx = build_request_ctx(request)

    usuario = (
        db.query(Usuario)
        .filter(func.lower(Usuario.email) == email_norm)
        .first()
    )

    # -----------------------------------------
    # Usuario no existe / inactivo
    # (no hay negocio_id confiable -> solo logs)
    # -----------------------------------------
    if not usuario or usuario.activo != 1:
        logger.info(
            "[AUTH] Login FAIL reason=user_not_found_or_inactive email=%s ip=%s",
            email_norm,
            request_ctx.get("ip"),
        )
        return templates.TemplateResponse(
            "app/login.html",
            {
                "request": request,
                "error": "Correo o contraseña incorrectos.",
                "user": None,
                "email": email_norm,
            },
            status_code=401,
        )

    # -----------------------------------------
    # Password incorrecta (aquí sí podemos auditar por negocio)
    # -----------------------------------------
    if not verify_password(password, usuario.password_hash):
        audit(
            db,
            action=AuditAction.AUTH_LOGIN_FAIL,
            user=_audit_user_dict(email=usuario.email, rol=usuario.rol, negocio_id=usuario.negocio_id),
            extra={"reason": "bad_password", "user_id": int(usuario.id)},
            request_ctx=request_ctx,
            commit=True,
        )
        return templates.TemplateResponse(
            "app/login.html",
            {
                "request": request,
                "error": "Correo o contraseña incorrectos.",
                "user": None,
                "email": email_norm,
            },
            status_code=401,
        )

    # -----------------------------------------
    # Estado del negocio (solo si NO es superadmin)
    # -----------------------------------------
    if usuario.rol != "superadmin":
        if (not usuario.negocio) or (usuario.negocio.estado != NegocioEstado.ACTIVO):
            audit(
                db,
                action=AuditAction.AUTH_LOGIN_FAIL,
                user=_audit_user_dict(email=usuario.email, rol=usuario.rol, negocio_id=usuario.negocio_id),
                extra={"reason": "business_not_active", "user_id": int(usuario.id)},
                request_ctx=request_ctx,
                commit=True,
            )
            return templates.TemplateResponse(
                "app/login.html",
                {
                    "request": request,
                    "error": "El negocio asociado está suspendido.",
                    "user": None,
                    "email": email_norm,
                },
                status_code=403,
            )

    # -----------------------------------------
    # Crear sesión + auditar OK (si hay negocio_id)
    # -----------------------------------------
    token_sesion = crear_sesion_db(db, usuario)

    # Baseline: superadmin global puede no tener negocio_id -> audit() no registra.
    audit(
        db,
        action=AuditAction.AUTH_LOGIN_OK,
        user=_audit_user_dict(email=usuario.email, rol=usuario.rol, negocio_id=usuario.negocio_id),
        entity_type="usuario",
        entity_id=int(usuario.id),
        extra={"negocio_id": int(usuario.negocio_id) if usuario.negocio_id else None},
        request_ctx=request_ctx,
        commit=True,
    )

    response = RedirectResponse(url="/app", status_code=302)
    crear_cookie_sesion(response, usuario, token_sesion)

    logger.info("[AUTH] Login OK user=%s rol=%s", usuario.email, usuario.rol)
    return response


# =========================================================
# LOGOUT
# =========================================================

@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: Session = Depends(get_db)):
    """
    Logout:
    - Revoca sesión activa en BD (si cookie válida)
    - Elimina cookie
    - Auditoría SOLO si hay negocio_id resoluble (tenant real)
    """
    request_ctx = build_request_ctx(request)

    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    current = get_current_user(request)
    audit_user = _audit_user_from_current(current)
    audit_negocio_id = _resolve_audit_negocio_id(audit_user)

    # helper local para: auditar solo si corresponde
    def _audit_logout(reason: str, extra: dict | None = None) -> None:
        if not audit_negocio_id:
            logger.info(
                "[AUTH] Logout (no-audit) reason=%s email=%s ip=%s",
                reason,
                audit_user.get("email"),
                request_ctx.get("ip"),
            )
            return

        audit(
            db,
            action=AuditAction.AUTH_LOGOUT,
            user={**audit_user, "negocio_id": audit_negocio_id},
            extra={"reason": reason, **(extra or {})},
            request_ctx=request_ctx,
            commit=True,
        )

    if cookie:
        try:
            raw = signer.unsign(cookie, max_age=SESSION_INACTIVITY_SECONDS)
            data = raw.decode("utf-8", errors="ignore")
            payload = json.loads(data) if data else {}

            user_id = payload.get("user_id")
            token_sesion = payload.get("token_sesion")

            try:
                user_id_int = int(user_id) if user_id is not None else None
            except Exception:
                user_id_int = None

            if user_id_int and token_sesion:
                (
                    db.query(SesionUsuario)
                    .filter(
                        SesionUsuario.usuario_id == user_id_int,
                        SesionUsuario.token_sesion == token_sesion,
                        SesionUsuario.activo == 1,
                    )
                    .update({SesionUsuario.activo: 0})
                )
                db.commit()

                _audit_logout("logout_ok", extra={"user_id": user_id_int})
                logger.info("[AUTH] Logout user_id=%s", user_id_int)

            else:
                _audit_logout("cookie_payload_invalid")

        except BadSignature:
            logger.debug("[AUTH] Cookie inválida en logout")
            _audit_logout("bad_cookie_signature")

        except Exception as exc:
            try:
                if db.in_transaction():
                    db.rollback()
            except Exception:
                pass

            logger.warning("[AUTH] Error en logout: %s", exc)
            _audit_logout("exception", extra={"error": str(exc)})

    else:
        _audit_logout("no_cookie")

    response = RedirectResponse(url="/app/login", status_code=302)
    _delete_session_cookie(response)
    return response
