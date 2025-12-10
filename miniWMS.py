# miniWMS.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.config import settings
from core.database import init_db
from contextlib import asynccontextmanager
from core.logging_config import setup_logging, logger

# Routers
from modules.basic_wms.routes.routes_auth import router as auth_router
from modules.basic_wms.routes.routes_superadmin import router as superadmin_router
from modules.basic_wms.routes.routes_users import router as users_router
from modules.basic_wms.routes.routes_register_business import router as register_business_router
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
from modules.basic_wms.routes.routes_health import router as health_router
from modules.basic_wms.routes.routes_backups import router as backups_router
from modules.basic_wms.routes.routes_export import router as export_router
from core.middleware.auth_redirect import redirect_middleware
from modules.inbound_orbion.routes import routes_inbound



# ============================
#   APP, STATIC, TEMPLATES
# ============================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🔹 Aquí va lo que antes estaba en @app.on_event("startup")
    init_db()  # crea tablas y seed de superadmin
    yield
    # 🔹 Aquí podrías poner código de "shutdown" si algún día lo necesitas


setup_logging()

app = FastAPI(
    title="MiniWMS",
    version="1.0.0",
    debug=settings.APP_DEBUG,
    lifespan=lifespan,
)

logger.info("miniWMS iniciado")

# Archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates globales (opcional; muchos routers ya tienen su propio Jinja2Templates)
templates = Jinja2Templates(directory="modules/basic_wms/templates")

# ============================
#   INCLUIR ROUTERS
# ============================

app.middleware("http")(redirect_middleware)
app.include_router(health_router)
app.include_router(backups_router)
app.include_router(auth_router)
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
