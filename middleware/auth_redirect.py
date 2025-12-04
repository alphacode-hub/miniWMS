# middleware/auth_redirect.py

from fastapi import Request
from starlette.responses import RedirectResponse

from security import get_current_user

# Rutas públicas que NO requieren autenticación (coincidencia exacta)
PUBLIC_EXACT_PATHS = {
    "/",         # root -> tu ruta ya redirige según usuario
    "/login",
    "/logout",
    "/registrar-negocio",
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

# Rutas del negocio (admin/operador)
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
    "/exportar",
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
        # No autenticado → login
        return RedirectResponse("/login")

    rol_real = user.get("rol_real") or user.get("rol")
    rol_efectivo = user.get("rol")
    impersonando = user.get("impersonando_negocio_id")

    # ============================================
    # A) SUPERADMIN en MODO NEGOCIO (impersonando)
    # ============================================
    if rol_real == "superadmin" and impersonando:
        # Puede entrar a /superadmin/* (por ejemplo para salir de modo negocio)
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return await call_next(request)

        # Puede entrar a todas las rutas de negocio como si fuera admin
        if any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
            return await call_next(request)

        # Cualquier otra cosa rara → lo mandamos al dashboard del negocio
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

        # Cualquier otra ruta (algo nuevo futuro, health, etc.) la dejamos pasar
        return await call_next(request)

    # ============================================
    # C) ADMIN DE NEGOCIO u OPERADOR
    # ============================================
    if rol_efectivo in ("admin", "operador"):
        # No pueden entrar a nada de /superadmin
        if any(path.startswith(prefix) for prefix in SUPERADMIN_PREFIXES):
            return RedirectResponse("/dashboard")

        # Rutas del negocio → OK
        if any(path.startswith(prefix) for prefix in ADMIN_PREFIXES):
            return await call_next(request)

        # Cualquier otra cosa rara → al dashboard del negocio
        return RedirectResponse("/dashboard")

    # ============================================
    # D) Fallback de seguridad (rol desconocido)
    # ============================================
    return RedirectResponse("/login")
