# security.py
from datetime import datetime, timedelta
import json
import bcrypt
import secrets

from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner, BadSignature
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models import Usuario, SesionUsuario

BCRYPT_MAX_LENGTH = 72
signer = TimestampSigner(settings.APP_SECRET_KEY)

# Tiempo máximo de inactividad de la sesión (en segundos)
SESSION_INACTIVITY_SECONDS = settings.SESSION_EXPIRATION_MINUTES * 60


def hash_password(password: str) -> str:
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")
    else:
        password_bytes = password

    password_bytes = password_bytes[:BCRYPT_MAX_LENGTH]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if isinstance(plain_password, str):
        plain_bytes = plain_password.encode("utf-8")
    else:
        plain_bytes = plain_password

    plain_bytes = plain_bytes[:BCRYPT_MAX_LENGTH]

    if isinstance(hashed_password, str):
        hashed_bytes = hashed_password.encode("utf-8")
    else:
        hashed_bytes = hashed_password

    try:
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except ValueError:
        return False


def crear_sesion_db(db: Session, usuario: Usuario) -> str:
    # Invalidar sesiones activas previas de este usuario
    db.query(SesionUsuario).filter(
        SesionUsuario.usuario_id == usuario.id,
        SesionUsuario.activo == 1
    ).update({SesionUsuario.activo: 0})

    token_sesion = secrets.token_urlsafe(settings.SESSION_TOKEN_BYTES)

    ahora = datetime.utcnow()
    nueva_sesion = SesionUsuario(
        usuario_id=usuario.id,
        token_sesion=token_sesion,
        activo=1,
        last_seen_at=ahora,  # aseguramos que no quede en NULL
    )
    db.add(nueva_sesion)
    db.commit()

    return token_sesion


def crear_cookie_sesion(response: RedirectResponse, usuario: Usuario, token_sesion: str):
    """
    Crea la cookie firmada de sesión.

    Nota: dejamos solo los datos mínimos para no exponer de más en la cookie.
    El resto se obtiene siempre desde la BD.
    """
    payload = {
        "user_id": usuario.id,
        "token_sesion": token_sesion,
        "ts": datetime.utcnow().isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signed = signer.sign(data).decode("utf-8")

    # Importante: max_age debería estar alineado con la expiración de la sesión
    max_age = settings.SESSION_MAX_AGE_SECONDS or SESSION_INACTIVITY_SECONDS

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=max_age,
        secure=settings.SESSION_COOKIE_SECURE,
    )


def get_current_user(request: Request):
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not cookie:
        return None

    # 1) Verificamos firma Y vencimiento (max_age)
    try:
        # Esto hace que la cookie NO sea válida después de X segundos
        data = signer.unsign(cookie, max_age=SESSION_INACTIVITY_SECONDS).decode("utf-8")
        payload = json.loads(data)
    except (BadSignature, json.JSONDecodeError):
        return None

    user_id = payload.get("user_id")
    token_sesion = payload.get("token_sesion")
    if not user_id or not token_sesion:
        return None

    db: Session = SessionLocal()
    try:
        # 2) Buscamos la sesión en BD
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

        # 3) Validamos inactividad por last_seen_at
        ahora = datetime.utcnow()
        if sesion.last_seen_at:
            delta = ahora - sesion.last_seen_at
            if delta.total_seconds() > SESSION_INACTIVITY_SECONDS:
                # Sesión expirada por inactividad -> la marcamos como inactiva
                sesion.activo = 0
                db.commit()
                return None

        # 4) Usuario activo
        usuario = db.query(Usuario).filter(
            Usuario.id == user_id,
            Usuario.activo == 1,
        ).first()
        if not usuario:
            return None

        # 5) Actualizamos last_seen_at
        sesion.last_seen_at = ahora
        db.commit()

        negocio_nombre = (
            usuario.negocio.nombre_fantasia
            if usuario.negocio
            else settings.SUPERADMIN_BUSINESS_NAME
        )

        # ⚠️ Aquí sí devolvemos info rica, pero solo en memoria.
        return {
            "id": usuario.id,
            "email": usuario.email,
            "negocio": negocio_nombre,
            "negocio_id": usuario.negocio_id,
            "rol": usuario.rol,
        }
    finally:
        db.close()


def is_superadmin(user: dict) -> bool:
    return user.get("rol") == "superadmin"


def require_superadmin(user: dict):
    if not user or user.get("rol") != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Solo superadmin puede acceder a esta sección",
        )


def require_user_dep(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )
    return user


def require_roles_dep(*allowed_roles: str):
    def dependency(request: Request):
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


def require_superadmin_dep(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )

    if user.get("rol") != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo superadmin puede acceder a esta sección.",
        )

    return user
