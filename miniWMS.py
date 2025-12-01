# miniWMS.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from database import init_db

# Routers
from routes_auth import router as auth_router
from routes_superadmin import router as superadmin_router
from routes_register_business import router as register_business_router
from routes_users import router as users_router
from routes_dashboard import router as dashboard_router
from routes_zones import router as zones_router
from routes_locations import router as locations_router
from routes_slots import router as slots_router
from routes_products import router as products_router
from routes_movements import router as movements_router
from routes_stock import router as stock_router
from routes_inventory import router as inventory_router
from routes_audit import router as audit_router
from routes_alerts import router as alerts_router


# ============================
#   APP, STATIC, TEMPLATES
# ============================

app = FastAPI(
    title="MiniWMS",
    version="1.0.0",
    debug=settings.APP_DEBUG,
)

# Archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates globales (opcional; muchos routers ya tienen su propio Jinja2Templates)
templates = Jinja2Templates(directory="templates")


# ============================
#   STARTUP
# ============================

@app.on_event("startup")
def on_startup():
    """
    Inicializa la base de datos al arrancar la app.
    """
    init_db()


# ============================
#   INCLUIR ROUTERS
# ============================

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
