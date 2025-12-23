# core/security.py
"""
Seguridad / Sesiones – ORBION (SaaS enterprise, baseline aligned)

✔ Hash/verify password con bcrypt (límite 72 bytes)
✔ Sesiones persistidas en BD (revocables)
✔ Cookie firmada (itsdangerous TimestampSigner)
✔ Control de inactividad (idle timeout) + max_age cookie
✔ Impersonación (superadmin -> modo negocio) vía payload firmado
✔ Dependencias FastAPI: require_user_dep / require_roles_dep / require_superadmin_dep

Baseline:
- La cookie guarda un payload mínimo (user_id + token_sesion + acting_negocio_id opcional).
- El rol efectivo se calcula desde BD (superadmin impersonando => admin).
- Contrato de impersonación:
    - Cookie: acting_negocio_id / acting_negocio_nombre
    - User dict expone:
        acting_negocio_id (source of truth)
        acting_negocio_nombre
        impersonando_negocio_id (alias compat)
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner, BadSignature
from sqlalchemy.orm import Session

from core.config import settings
from core.database import SessionLocal
from core.models import Usuario, SesionUsuario
from core.models.time import utcnow

# bcrypt solo usa los primeros 72 bytes
BCRYPT_MAX_LENGTH = 72

# signer de cookie (firma + timestamp)
signer = TimestampSigner(settings.APP_SECRET_KEY)

# tiempo máximo de inactividad (idle timeout)
SESSION_INACTIVITY_SECONDS = int(settings.SESSION_EXPIRATION_MINUTES) * 60


# =========================================================
# DATETIME NORMALIZATION (UTC tz-aware)
# =========================================================

def _ensure_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normaliza datetime a UTC tz-aware.
    - SQLite puede devolver naive.
    - Si dt es naive, asumimos UTC.
    - Si dt es aware, lo convertimos a UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utcnow_aware() -> datetime:
    """
    Wrapper defensivo: utcnow() del proyecto debería ser tz-aware.
    Aseguramos UTC tz-aware igual.
    """
    return _ensure_utc_aware(utcnow())  # type: ignore[arg-type]


# =========================================================
# PASSWORDS
# =========================================================

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


# =========================================================
# SESSION DB
# =========================================================

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
    ahora = _utcnow_aware()

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


# =========================================================
# COOKIE PAYLOAD
# =========================================================

def _default_payload(usuario: Usuario, token_sesion: str) -> dict[str, Any]:
    """
    Payload mínimo de cookie (firmado).
    """
    return {
        "user_id": int(usuario.id),
        "token_sesion": token_sesion,
        # Timestamp informativo
        "ts": _utcnow_aware().isoformat(),
        # Impersonación (source of truth)
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
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else None
    except (BadSignature, json.JSONDecodeError, UnicodeDecodeError):
        return None
    except Exception:
        return None


def _encode_cookie_payload(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return signer.sign(data).decode("utf-8")


def _set_session_cookie_from_payload(response: RedirectResponse, payload: dict) -> None:
    signed = _encode_cookie_payload(payload)

    max_age = (
        int(settings.SESSION_MAX_AGE_SECONDS)
        if settings.SESSION_MAX_AGE_SECONDS
        else SESSION_INACTIVITY_SECONDS
    )

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


# =========================================================
# CURRENT USER (AUTH)
# =========================================================

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

    # defensivo
    try:
        user_id_int = int(user_id)
    except Exception:
        return None

    db: Session = SessionLocal()
    try:
        sesion: SesionUsuario | None = (
            db.query(SesionUsuario)
            .filter(
                SesionUsuario.usuario_id == user_id_int,
                SesionUsuario.token_sesion == token_sesion,
                SesionUsuario.activo == 1,
            )
            .first()
        )
        if not sesion:
            return None

        ahora = _utcnow_aware()

        last_seen = _ensure_utc_aware(getattr(sesion, "last_seen_at", None))
        if last_seen:
            delta = ahora - last_seen
            if delta.total_seconds() > SESSION_INACTIVITY_SECONDS:
                sesion.activo = 0
                db.commit()
                return None

        usuario: Usuario | None = (
            db.query(Usuario)
            .filter(Usuario.id == user_id_int, Usuario.activo == 1)
            .first()
        )
        if not usuario:
            return None

        # touch last_seen_at
        sesion.last_seen_at = ahora
        db.commit()

        rol_real = (usuario.rol or "").strip()
        rol_efectivo = rol_real

        negocio_id = usuario.negocio_id
        negocio_nombre = (
            usuario.negocio.nombre_fantasia
            if getattr(usuario, "negocio", None)
            else settings.SUPERADMIN_BUSINESS_NAME
        )

        acting_id_int: int | None = None
        if acting_negocio_id:
            try:
                acting_id_int = int(acting_negocio_id)
            except Exception:
                acting_id_int = None

        # Impersonación (superadmin -> admin en contexto negocio)
        if rol_real == "superadmin" and acting_id_int:
            negocio_id = acting_id_int
            if acting_negocio_nombre:
                negocio_nombre = str(acting_negocio_nombre)
            rol_efectivo = "admin"

        return {
            "id": int(usuario.id),
            "email": usuario.email,
            "negocio": negocio_nombre,
            "negocio_id": negocio_id,
            "rol": rol_efectivo,
            "rol_real": rol_real,

            # ✅ Source of truth
            "acting_negocio_id": acting_id_int,
            "acting_negocio_nombre": str(acting_negocio_nombre) if acting_negocio_nombre else None,

            # ✅ Alias compat (muchas rutas viejas lo miran)
            "impersonando_negocio_id": acting_id_int,
        }

    finally:
        db.close()


# =========================================================
# ROLE HELPERS
# =========================================================

def _norm_role(x: Any) -> str:
    try:
        return str(x or "").strip().lower()
    except Exception:
        return ""


def is_superadmin(user: dict) -> bool:
    return _norm_role(user.get("rol_real") or user.get("rol")) == "superadmin"


def is_superadmin_global(user: dict) -> bool:
    # Global => no está impersonando
    return is_superadmin(user) and not user.get("acting_negocio_id")


def is_admin(user: dict) -> bool:
    return _norm_role(user.get("rol")) == "admin"


def is_operator(user: dict) -> bool:
    return _norm_role(user.get("rol")) == "operador"


# =========================================================
# DEPENDENCIES (FASTAPI)
# =========================================================

def require_user_dep(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )
    return user


def require_roles_dep(*allowed_roles: str):
    allowed = {str(r).strip().lower() for r in allowed_roles}

    def dependency(request: Request) -> dict:
        user = get_current_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No autenticado.",
            )

        rol = _norm_role(user.get("rol"))
        if rol not in allowed:
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

    if _norm_role(user.get("rol_real") or user.get("rol")) != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo superadmin puede acceder a esta sección.",
        )

    return user


# =========================================================
# IMPERSONACIÓN (MODO NEGOCIO)
# =========================================================

def activar_modo_negocio(
    response: RedirectResponse,
    request: Request,
    negocio_id: int,
    negocio_nombre: str,
) -> bool:
    """
    Activa impersonación en cookie firmada.
    Retorna True si se aplicó, False si no había sesión válida.
    """
    payload = _get_session_payload_from_request(request)
    if not payload:
        return False

    payload["acting_negocio_id"] = int(negocio_id)
    payload["acting_negocio_nombre"] = str(negocio_nombre)
    payload["ts"] = _utcnow_aware().isoformat()

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
    payload["ts"] = _utcnow_aware().isoformat()

    _set_session_cookie_from_payload(response, payload)
    return True
