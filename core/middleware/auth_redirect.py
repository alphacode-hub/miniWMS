# core/middleware/auth_redirect.py
"""
Middleware de redirección / autorización por rutas – ORBION (SaaS enterprise)

✔ Public routes (exact + prefixes)
✔ Separación clara:
  - Superadmin global
  - Superadmin impersonando negocio
  - Admin/Operador (WMS full)
  - Roles inbound-only (solo /inbound)
✔ Redirecciones consistentes (sin loops)
✔ Login centralizado en /app/login
"""

from __future__ import annotations

from fastapi import Request
from starlette.responses import RedirectResponse, Response

from core.security import get_current_user


# ============================
# PUBLIC ROUTES (NO AUTH)
# ============================

PUBLIC_EXACT_PATHS: set[str] = {
    "/",                     # Landing ORBION
    "/app/login",
    "/app/logout",
    "/app/registrar-negocio",
    "/favicon.ico",
}

PUBLIC_PREFIXES: tuple[str, ...] = (
    "/static",
    "/docs",
    "/openapi.json",
)


# ============================
# ROUTE GROUPS
# ============================

SUPERADMIN_PREFIXES: tuple[str, ...] = (
    "/superadmin",
)

# Hub (menú) – lo permitimos a casi todos ya autenticados
HUB_EXACT_PATHS: set[str] = {
    "/app",
}

# Rutas de negocio (WMS + inbound)
ADMIN_PREFIXES: tuple[str, ...] = (
    "/dashboard",
    "/productos",
    "/movimientos",
    "/ubicaciones",
    "/exportar",
    "/auditoria",
    "/usuarios",
    "/stock",
    "/inventario",
    "/alertas",
    "/zonas",
    "/slots",
    "/transferencia",
    "/inbound",
)

# Roles restringidos SOLO inbound
INBOUND_ONLY_ROLES: tuple[str, ...] = (
    "operador_inbound",
    "supervisor_inbound",
    "auditor_inbound",
    "transportista",
)


# ============================
# HELPERS
# ============================

def _is_public(path: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _starts_with_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(p) for p in prefixes)


def _redirect(url: str) -> RedirectResponse:
    # 302 por defecto es suficiente y compatible
    return RedirectResponse(url=url, status_code=302)


# ============================
# MIDDLEWARE
# ============================

async def redirect_middleware(request: Request, call_next) -> Response:
    path = request.url.path

    # 1) Public routes -> pasan directo
    if _is_public(path):
        return await call_next(request)

    # 2) Auth
    user = get_current_user(request)
    if not user:
        return _redirect("/app/login")

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    impersonando = bool(user.get("impersonando_negocio_id"))

    # ============================
    # A) SUPERADMIN impersonando
    # ============================
    if rol_real == "superadmin" and impersonando:
        # Puede entrar al superadmin (para salir del modo negocio / consola)
        if _starts_with_any(path, SUPERADMIN_PREFIXES):
            return await call_next(request)

        # Puede acceder rutas de negocio completas (incluye inbound)
        if _starts_with_any(path, ADMIN_PREFIXES):
            return await call_next(request)

        # Puede entrar al hub /app
        if path in HUB_EXACT_PATHS:
            return await call_next(request)

        # Default seguro
        return _redirect("/dashboard")

    # ============================
    # B) SUPERADMIN global
    # ============================
    if rol_real == "superadmin":
        # Acceso libre a panel superadmin
        if _starts_with_any(path, SUPERADMIN_PREFIXES):
            return await call_next(request)

        # Si intenta entrar a rutas negocio, lo enviamos al panel global
        if _starts_with_any(path, ADMIN_PREFIXES):
            return _redirect("/superadmin/dashboard")

        # Hub y otras rutas internas (health, etc.) permitidas
        return await call_next(request)

    # ============================
    # C) ADMIN / OPERADOR (WMS full)
    # ============================
    if rol_efectivo in {"admin", "operador"}:
        # Bloqueo duro a /superadmin
        if _starts_with_any(path, SUPERADMIN_PREFIXES):
            return _redirect("/app")

        if path in HUB_EXACT_PATHS:
            return await call_next(request)

        if _starts_with_any(path, ADMIN_PREFIXES):
            return await call_next(request)

        return _redirect("/app")

    # ============================
    # D) ROLES inbound-only
    # ============================
    if rol_efectivo in INBOUND_ONLY_ROLES:
        # Bloqueo a superadmin
        if _starts_with_any(path, SUPERADMIN_PREFIXES):
            return _redirect("/app")

        if path in HUB_EXACT_PATHS:
            return await call_next(request)

        # Solo inbound
        if path.startswith("/inbound"):
            return await call_next(request)

        return _redirect("/inbound")

    # ============================
    # E) fallback (rol desconocido)
    # ============================
    return _redirect("/app/login")
