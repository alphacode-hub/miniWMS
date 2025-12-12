# core/middleware/auth_redirect.py

from fastapi import Request
from starlette.responses import RedirectResponse

from core.security import get_current_user

# Rutas públicas que NO requieren autenticación (coincidencia exacta)
PUBLIC_EXACT_PATHS = {
    "/",                # Landing ORBION
    # Rutas nuevas bajo /app
    "/app/login",       # Login oficial
    "/app/logout",
    "/app/registrar-negocio",
    "/favicon.ico",
}

# Prefijos públicos (static, docs, etc.)
PUBLIC_PREFIXES = (
    "/static",
    "/docs",
    "/openapi.json",
)

# Rutas del superadmin (panel global, gestión de negocios, etc.)
SUPERADMIN_PREFIXES = (
    "/superadmin",
)

# Rutas del negocio (admin/operador FULL WMS + inbound)
ADMIN_PREFIXES = (
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
    "/inbound",   # 👈 todo lo que sea inbound va bajo este prefijo
)

# 🆕 Roles especializados SOLO inbound
INBOUND_ONLY_ROLES = (
    "operador_inbound",
    "supervisor_inbound",
    "auditor_inbound",
    "transportista",
)


async def redirect_middleware(request: Request, call_next):
    path = request.url.path

    # 1) RUTAS PÚBLICAS → pasan directo
    if path in PUBLIC_EXACT_PATHS or any(
        path.startswith(prefix) for prefix in PUBLIC_PREFIXES
    ):
        return await call_next(request)

    # 2) OBTENER USUARIO
    user = get_current_user(request)
    if not user:
        # No autenticado → SIEMPRE al login nuevo
        return RedirectResponse("/app/login")

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    impersonando = user.get("impersonando_negocio_id")

    # ============================================
    # A) SUPERADMIN en MODO NEGOCIO (impersonando)
    # ============================================
    if rol_real == "superadmin" and impersonando:
        # Puede entrar a /superadmin/* (para salir de modo negocio o ver consola)
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return await call_next(request)

        # Rutas de negocio (incluye /inbound) → OK
        if any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
            return await call_next(request)

        # Hub ORBION /app → permitido
        if path == "/app":
            return await call_next(request)

        # Cualquier otra cosa rara → dashboard del negocio
        return RedirectResponse("/dashboard")

    # ============================================
    # B) SUPERADMIN GLOBAL (SIN modo negocio)
    # ============================================
    if rol_real == "superadmin":
        # Puede acceder libremente a /superadmin/*
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return await call_next(request)

        # Si intenta ir a rutas de negocio, lo regresamos a su panel global
        if any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
            return RedirectResponse("/superadmin/dashboard")

        # Cualquier otra ruta (hub /app, health, etc.) la dejamos pasar
        return await call_next(request)

    # ============================================
    # C) ADMIN DE NEGOCIO u OPERADOR (FULL WMS)
    # ============================================
    if rol_efectivo in ("admin", "operador"):
        # No pueden entrar a nada de /superadmin
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return RedirectResponse("/app")

        # Hub ORBION /app → permitido
        if path == "/app":
            return await call_next(request)

        # Rutas del negocio → OK (WMS completo + inbound)
        if any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
            return await call_next(request)

        # Cualquier otra cosa rara → al hub ORBION
        return RedirectResponse("/app")

    # ============================================
    # D) ROLES ESPECIALIZADOS SOLO INBOUND
    #    (operador_inbound, supervisor_inbound, auditor_inbound, transportista)
    # ============================================
    if rol_efectivo in INBOUND_ONLY_ROLES:
        # Nunca pueden entrar a /superadmin
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return RedirectResponse("/app")

        # Hub ORBION /app → permitido (para menú, cambio de clave, etc.)
        if path == "/app":
            return await call_next(request)

        # Solo pueden acceder a todo lo que esté bajo /inbound
        if path.startswith("/inbound"):
            return await call_next(request)

        # Cualquier otra ruta de negocio (productos, stock, etc.) → lo devolvemos a inbound
        return RedirectResponse("/inbound")

    # ============================================
    # E) Fallback de seguridad (rol desconocido)
    # ============================================
    return RedirectResponse("/app/login")
