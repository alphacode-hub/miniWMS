# main.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from core.config import settings
from core.database import init_db
from core.logging_config import setup_logging, logger
from core.templates import create_templates  # ✅ nuevo
from core.web import templates

# Routers
from core.routes import routes_health
from core.routes.routes_auth import router as auth_router
from core.routes.routes_app_hub import router as hub_router
from core.routes.routes_superadmin import router as superadmin_router
from modules.basic_wms.routes.routes_users import router as users_router
from core.routes.routes_business import router as register_business_router
from modules.basic_wms.routes.routes_dashboard import router as dashboard_router
from modules.basic_wms.routes.routes_zones import router as zones_router
from modules.basic_wms.routes.routes_locations import router as locations_router
from modules.basic_wms.routes.routes_slots import router as slots_router
from modules.basic_wms.routes.routes_products import router as products_router
from modules.basic_wms.routes.routes_movements import router as movements_router
from modules.basic_wms.routes.routes_stock import router as stock_router
from modules.basic_wms.routes.routes_inventory import router as inventory_router
from modules.basic_wms.routes.routes_audit import router as audit_router
from modules.basic_wms.routes.routes_alerts import router as alerts_router
from modules.basic_wms.routes.routes_backups import router as backups_router
from modules.basic_wms.routes.routes_export import router as export_router

from core.middleware.auth_redirect import redirect_middleware
from modules.inbound_orbion.routes import routes_inbound

from core.bootstrap import ensure_superadmin



# ============================
#   APP, STATIC, TEMPLATES
# ============================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    ensure_superadmin(
        email=settings.SUPERADMIN_EMAIL,
        password=settings.SUPERADMIN_PASSWORD,
        negocio_nombre=settings.SUPERADMIN_BUSINESS_NAME,  
        user_display_name=settings.SUPERADMIN_DISPLAY_NAME,  
    )

    yield


setup_logging()

app = FastAPI(
    title="ORBION",
    version="1.0.0",
    debug=settings.APP_DEBUG,
    lifespan=lifespan,
)

logger.info("ORBION iniciado")

# Static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates (global)
BASE_DIR = Path(__file__).resolve().parent
templates = create_templates(BASE_DIR)


# ============================
#   RUTA PÚBLICA: LANDING
# ============================

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse(
        "public/landing.html",
        {
            "request": request,
            # opcional: puedes pasar flags/metadata aquí si quieres
        },
    )


# ============================
#   RUTA FAVICON
# ============================

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/img/favicon.ico")


# ============================
#   MIDDLEWARE + ROUTERS
# ============================

app.middleware("http")(redirect_middleware)

app.include_router(routes_health.router)
app.include_router(backups_router)
app.include_router(auth_router)
app.include_router(hub_router)
app.include_router(superadmin_router)
app.include_router(register_business_router)
app.include_router(users_router)
app.include_router(dashboard_router)
app.include_router(zones_router)
app.include_router(locations_router)
app.include_router(slots_router)
app.include_router(products_router)
app.include_router(movements_router)
app.include_router(stock_router)
app.include_router(inventory_router)
app.include_router(audit_router)
app.include_router(alerts_router)
app.include_router(export_router)
app.include_router(routes_inbound.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.APP_DEBUG,
    )


for r in app.routes:
    if "inbound" in getattr(r, "path", ""):
        print(r.path)
