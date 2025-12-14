# core/security.py
"""
Seguridad / Sesiones – ORBION (SaaS enterprise)

✔ Hash/verify password con bcrypt (límite 72 bytes)
✔ Sesiones persistidas en BD (revocables)
✔ Cookie firmada (itsdangerous TimestampSigner)
✔ Control de inactividad (idle timeout) + max_age cookie
✔ Impersonación (superadmin -> modo negocio) vía payload firmado
✔ Dependencias FastAPI: require_user_dep / require_roles_dep / require_superadmin_dep

NOTA:
- El token real se valida SIEMPRE contra BD (cookie solo lleva datos mínimos firmados).
- En enterprise, la cookie NO contiene el rol definitivo (se calcula desde BD).
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner, BadSignature
from sqlalchemy.orm import Session

from core.config import settings
from core.database import SessionLocal
from core.models import Usuario, SesionUsuario

# bcrypt solo usa los primeros 72 bytes
BCRYPT_MAX_LENGTH = 72

# signer de cookie (firma + timestamp)
signer = TimestampSigner(settings.APP_SECRET_KEY)

# tiempo máximo de inactividad (idle timeout)
SESSION_INACTIVITY_SECONDS = int(settings.SESSION_EXPIRATION_MINUTES) * 60


# ============================
# PASSWORDS
# ============================

def _to_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


def hash_password(password: str) -> str:
    password_bytes = _to_bytes(password)[:BCRYPT_MAX_LENGTH]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        plain_bytes = _to_bytes(plain_password)[:BCRYPT_MAX_LENGTH]
        hashed_bytes = _to_bytes(hashed_password)
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


# ============================
# SESSION DB
# ============================

def crear_sesion_db(db: Session, usuario: Usuario) -> str:
    """
    Crea una nueva sesión y revoca cualquier sesión activa anterior del usuario.
    Retorna token_sesion (secreto) almacenado en BD.
    """
    db.query(SesionUsuario).filter(
        SesionUsuario.usuario_id == usuario.id,
        SesionUsuario.activo == 1,
    ).update({SesionUsuario.activo: 0})

    token_sesion = secrets.token_urlsafe(int(settings.SESSION_TOKEN_BYTES))

    ahora = datetime.utcnow()
    nueva_sesion = SesionUsuario(
        usuario_id=usuario.id,
        token_sesion=token_sesion,
        activo=1,
        created_at=ahora,
        last_seen_at=ahora,
    )
    db.add(nueva_sesion)
    db.commit()

    return token_sesion


def invalidar_sesion_db(db: Session, user_id: int, token_sesion: str) -> None:
    """
    Revoca una sesión específica.
    """
    db.query(SesionUsuario).filter(
        SesionUsuario.usuario_id == user_id,
        SesionUsuario.token_sesion == token_sesion,
        SesionUsuario.activo == 1,
    ).update({SesionUsuario.activo: 0})
    db.commit()


# ============================
# COOKIE PAYLOAD
# ============================

def _default_payload(usuario: Usuario, token_sesion: str) -> dict[str, Any]:
    """
    Payload mínimo de cookie (firmado).
    """
    return {
        "user_id": usuario.id,
        "token_sesion": token_sesion,
        # Timestamp de auditoría (no es el que valida expiración; eso lo hace TimestampSigner)
        "ts": datetime.utcnow().isoformat(),
        # Impersonación (opcionales)
        "acting_negocio_id": None,
        "acting_negocio_nombre": None,
    }


def crear_cookie_sesion(response: RedirectResponse, usuario: Usuario, token_sesion: str) -> None:
    payload = _default_payload(usuario, token_sesion)
    _set_session_cookie_from_payload(response, payload)


def _decode_cookie_payload(cookie_value: str) -> dict | None:
    """
    Verifica firma + max_age y retorna payload dict.
    """
    try:
        raw = signer.unsign(cookie_value, max_age=SESSION_INACTIVITY_SECONDS)
        data = raw.decode("utf-8", errors="strict")
        return json.loads(data)
    except (BadSignature, json.JSONDecodeError, UnicodeDecodeError):
        return None
    except Exception:
        return None


def _encode_cookie_payload(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return signer.sign(data).decode("utf-8")


def _set_session_cookie_from_payload(response: RedirectResponse, payload: dict) -> None:
    signed = _encode_cookie_payload(payload)

    # Max age de cookie (puede ser distinto al idle timeout interno)
    max_age = int(settings.SESSION_MAX_AGE_SECONDS) if settings.SESSION_MAX_AGE_SECONDS else SESSION_INACTIVITY_SECONDS

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=max_age,
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )


def _get_session_payload_from_request(request: Request) -> dict | None:
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return _decode_cookie_payload(cookie)


# ============================
# CURRENT USER (AUTH)
# ============================

def get_current_user(request: Request) -> dict | None:
    """
    Retorna un dict de usuario efectivo para el request, o None.

    Reglas enterprise:
    - Cookie válida => payload (user_id + token)
    - Token debe existir en BD como sesión activa
    - last_seen_at controla idle timeout (además del TimestampSigner max_age)
    - Superadmin puede impersonar un negocio (rol efectivo = admin)
    """
    payload = _get_session_payload_from_request(request)
    if not payload:
        return None

    user_id = payload.get("user_id")
    token_sesion = payload.get("token_sesion")
    acting_negocio_id = payload.get("acting_negocio_id")
    acting_negocio_nombre = payload.get("acting_negocio_nombre")

    if not user_id or not token_sesion:
        return None

    db: Session = SessionLocal()
    try:
        sesion = (
            db.query(SesionUsuario)
            .filter(
                SesionUsuario.usuario_id == user_id,
                SesionUsuario.token_sesion == token_sesion,
                SesionUsuario.activo == 1,
            )
            .first()
        )
        if not sesion:
            return None

        ahora = datetime.utcnow()

        # Idle timeout por last_seen_at (revoca)
        if sesion.last_seen_at:
            delta = ahora - sesion.last_seen_at
            if delta.total_seconds() > SESSION_INACTIVITY_SECONDS:
                sesion.activo = 0
                db.commit()
                return None

        usuario = (
            db.query(Usuario)
            .filter(Usuario.id == user_id, Usuario.activo == 1)
            .first()
        )
        if not usuario:
            return None

        # touch
        sesion.last_seen_at = ahora
        db.commit()

        rol_real = usuario.rol
        rol_efectivo = rol_real

        negocio_id = usuario.negocio_id
        negocio_nombre = (
            usuario.negocio.nombre_fantasia
            if getattr(usuario, "negocio", None)
            else settings.SUPERADMIN_BUSINESS_NAME
        )

        # Impersonación (superadmin -> admin en contexto negocio)
        if rol_real == "superadmin" and acting_negocio_id:
            negocio_id = acting_negocio_id
            if acting_negocio_nombre:
                negocio_nombre = acting_negocio_nombre
            rol_efectivo = "admin"

        return {
            "id": usuario.id,
            "email": usuario.email,
            "negocio": negocio_nombre,
            "negocio_id": negocio_id,
            "rol": rol_efectivo,
            "rol_real": rol_real,
            "impersonando_negocio_id": acting_negocio_id,
        }

    finally:
        db.close()


# ============================
# ROLE HELPERS
# ============================

def is_superadmin(user: dict) -> bool:
    return (user.get("rol_real") or user.get("rol")) == "superadmin"


def is_superadmin_global(user: dict) -> bool:
    return is_superadmin(user) and not user.get("impersonando_negocio_id")


def is_admin(user: dict) -> bool:
    return user.get("rol") == "admin"


def is_operator(user: dict) -> bool:
    return user.get("rol") == "operador"


# ============================
# DEPENDENCIES (FASTAPI)
# ============================

def require_user_dep(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )
    return user


def require_roles_dep(*allowed_roles: str):
    def dependency(request: Request) -> dict:
        user = get_current_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No autenticado.",
            )

        rol = user.get("rol")
        if rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para acceder a este recurso.",
            )
        return user

    return dependency


def require_superadmin_dep(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )

    if (user.get("rol_real") or user.get("rol")) != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo superadmin puede acceder a esta sección.",
        )

    return user


# ============================
# IMPERSONACIÓN (MODO NEGOCIO)
# ============================

def activar_modo_negocio(response: RedirectResponse, request: Request, negocio_id: int, negocio_nombre: str) -> bool:
    """
    Activa impersonación en cookie firmada.
    Retorna True si se aplicó, False si no había sesión válida.
    """
    payload = _get_session_payload_from_request(request)
    if not payload:
        return False

    payload["acting_negocio_id"] = negocio_id
    payload["acting_negocio_nombre"] = negocio_nombre
    payload["ts"] = datetime.utcnow().isoformat()

    _set_session_cookie_from_payload(response, payload)
    return True


def desactivar_modo_negocio(response: RedirectResponse, request: Request) -> bool:
    """
    Desactiva impersonación.
    """
    payload = _get_session_payload_from_request(request)
    if not payload:
        return False

    payload["acting_negocio_id"] = None
    payload["acting_negocio_nombre"] = None
    payload["ts"] = datetime.utcnow().isoformat()

    _set_session_cookie_from_payload(response, payload)
    return True
