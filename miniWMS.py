# miniWMS.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse

from core.config import settings
from core.database import init_db
from core.logging_config import setup_logging, logger

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



# ============================
#   APP, STATIC, TEMPLATES
# ============================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🔹 Startup
    init_db()  # crea tablas y seed de superadmin
    yield
    # 🔹 Aquí podrías poner código de "shutdown" si algún día lo necesitas


setup_logging()

app = FastAPI(
    title="ORBION",
    version="1.0.0",
    debug=settings.APP_DEBUG,
    lifespan=lifespan,
)

logger.info("ORBION iniciado")

# Archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates globales (APUNTANDO AL NUEVO DIRECTORIO /templates)
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   RUTA PÚBLICA: LANDING
# ============================

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """
    Landing pública de ORBION.
    Debe existir templates/public/landing.html
    que extienda base/base_public.html
    """
    return templates.TemplateResponse(
        "public/landing.html",
        {"request": request},
    )

# ============================
#   RUTA FAVICON
# ============================

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico")

# ============================
#   INCLUIR ROUTERS
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


# ============================
#      MAIN (modo script)
# ============================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "miniWMS:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.APP_DEBUG,  # reload solo en desarrollo
    )
