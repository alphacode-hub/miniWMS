
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import TimestampSigner, BadSignature
from pathlib import Path
from datetime import datetime, date, timedelta

from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey ,func, Date, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

from pathlib import Path
from fastapi import HTTPException

import json

BASE_DIR = Path(__file__).resolve().parent

# ============================
#   BASE DE DATOS (SQLite)
# ============================

SQLALCHEMY_DATABASE_URL = f"sqlite:///{BASE_DIR / 'miniWMS.db'}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}  # Necesario para SQLite en FastAPI
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def apply_schema_updates(engine):
    """
    Pequeño sistema de 'migraciones' para SQLite.
    Revisa si existen ciertas columnas; si no, las agrega con ALTER TABLE.

    Esto se ejecuta una vez al inicio de la app y permite
    evolucionar el esquema sin borrar la base de datos.
    """
    with engine.connect() as conn:

        def column_exists(table_name: str, column_name: str) -> bool:
            result = conn.execute(text(f"PRAGMA table_info({table_name});"))
            for row in result:
                # row[1] = nombre de la columna
                if row[1] == column_name:
                    return True
            return False

        # ==============================
        # Tabla productos: stock_min, stock_max
        # ==============================
        if not column_exists("productos", "stock_min"):
            conn.execute(text("ALTER TABLE productos ADD COLUMN stock_min INTEGER;"))
            print("[MIGRACION] Agregada columna productos.stock_min")

        if not column_exists("productos", "stock_max"):
            conn.execute(text("ALTER TABLE productos ADD COLUMN stock_max INTEGER;"))
            print("[MIGRACION] Agregada columna productos.stock_max")

        # ==============================
        # Tabla movimientos: fecha_vencimiento
        # ==============================
        if not column_exists("movimientos", "fecha_vencimiento"):
            # En SQLite, DATE es básicamente texto ISO-8601; SQLAlchemy se encarga de parsear
            conn.execute(text("ALTER TABLE movimientos ADD COLUMN fecha_vencimiento DATE;"))
            print("[MIGRACION] Agregada columna movimientos.fecha_vencimiento")

        # ==============================
        # Tabla zonas: sigla (si la estás usando)
        # ==============================
        if not column_exists("zonas", "sigla"):
            conn.execute(text("ALTER TABLE zonas ADD COLUMN sigla TEXT;"))
            print("[MIGRACION] Agregada columna zonas.sigla")

        # ==============================
        # Tabla ubicaciones: sigla (si la estás usando)
        # ==============================
        if not column_exists("ubicaciones", "sigla"):
            conn.execute(text("ALTER TABLE ubicaciones ADD COLUMN sigla TEXT;"))
            print("[MIGRACION] Agregada columna ubicaciones.sigla")

        # ==============================
        # Tabla slots: capacidad (por si en algún momento la agregas después)
        # ==============================
        if not column_exists("slots", "capacidad"):
            conn.execute(text("ALTER TABLE slots ADD COLUMN capacidad INTEGER;"))
            print("[MIGRACION] Agregada columna slots.capacidad")

        # ==============================
        # Tabla productos: activo (soft delete)
        # ==============================
        if not column_exists("productos", "activo"):
            conn.execute(text("ALTER TABLE productos ADD COLUMN activo INTEGER DEFAULT 1;"))
            print("[MIGRACION] Agregada columna productos.activo")


      

# ============================
#   CLASES DEL MODELO
# ============================

class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(DateTime, default=datetime.utcnow, index=True)
    negocio = Column(String, index=True)
    usuario = Column(String, index=True)
    accion = Column(String, index=True)
    detalle = Column(String)  # JSON o texto libre



class Zona(Base):
    __tablename__ = "zonas"

    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True)
    nombre = Column(String, index=True)
    sigla = Column(String, index=True)
    ubicaciones = relationship("Ubicacion", back_populates="zona", cascade="all, delete-orphan")


class Ubicacion(Base):
    __tablename__ = "ubicaciones"

    id = Column(Integer, primary_key=True, index=True)
    zona_id = Column(Integer, ForeignKey("zonas.id"), index=True)
    nombre = Column(String, index=True)
    sigla = Column(String, index=True)
    zona = relationship("Zona", back_populates="ubicaciones")
    slots = relationship("Slot", back_populates="ubicacion", cascade="all, delete-orphan")



class Slot(Base):
    __tablename__ = "slots"

    id = Column(Integer, primary_key=True, index=True)
    ubicacion_id = Column(Integer, ForeignKey("ubicaciones.id"), index=True)
    codigo = Column(String, index=True)  # Ej: C1, C2, C3...
    capacidad = Column(Integer, default=None)  # opcional
    codigo_full = Column(String, index=True)
    ubicacion = relationship("Ubicacion", back_populates="slots")



class Producto(Base):
    __tablename__ = "productos"
    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True)
    nombre = Column(String, index=True)
    unidad = Column(String, default="unidad")
    stock_min = Column(Integer, nullable=True)
    stock_max = Column(Integer, nullable=True)
    activo = Column(Integer, default=1)



class Movimiento(Base):
    __tablename__ = "movimientos"
    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True)
    usuario = Column(String, index=True)
    tipo = Column(String, index=True)
    producto = Column(String, index=True)
    cantidad = Column(Integer)
    zona = Column(String)
    fecha = Column(DateTime, default=datetime.utcnow)
    fecha_vencimiento = Column(Date, nullable=True)


# Crear tablas (si no existen)
Base.metadata.create_all(bind=engine)
# Aplicar actualizaciones de esquema (no destructivas)
apply_schema_updates(engine)


# Dependencia para obtener sesión de BD en cada request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI()

# Servir archivos estáticos (si los necesitas luego)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# 🔑 Firmador simple para la cookie de sesion
SECRET_KEY = "VeuoeH6L"
signer = TimestampSigner(SECRET_KEY)

# 🧪 Usuarios de prueba (MVP)
# En el futuro esto vendrá de Azure SQL / SQLite
FAKE_USERS = {
    "t1@t1.cl": {
        "password": "1234",
        "negocio": "Negocio Demo 1",
        "rol": "admin",        # dueño / admin Negocio Demo 1
    },
    "t12@t1.cl": {
        "password": "1234",
        "negocio": "Negocio Demo 1",
        "rol": "operador",     # operador Negocio Demo 1
    },
    "t2@t2.cl": {
        "password": "1234",
        "negocio": "Negocio Demo 2",
        "rol": "admin",        # admin Negocio Demo 2
    },
    # Ejemplo de superadmin (opcional)
    "root@wms.cl": {
        "password": "1234",
        "negocio": "Negocio Demo 1",  # base por defecto
        "rol": "superadmin",
    },
}


# ------------ Helpers de sesion ------------

def get_current_user(request: Request):
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        value = signer.unsign(cookie).decode("utf-8")
        # value = email del usuario
        user = FAKE_USERS.get(value)
        if not user:
            return None
        return {"email": value, "negocio": user["negocio"], "rol": user.get("rol", "operador"),}
    except BadSignature:
        return None

def require_role(user: dict, allowed_roles: tuple[str, ...]):
    """
    Lanza 403 si el usuario no tiene un rol permitido.
    allowed_roles: ej. ("admin", "superadmin")
    """
    if user["rol"] not in allowed_roles:
        raise HTTPException(status_code=403, detail="No autorizado para esta acción")

def login_required(request: Request):
    user = get_current_user(request)
    if not user:
        # si no hay sesion, redirige al login
        return RedirectResponse(url="/login", status_code=302)
    return user

def registrar_auditoria(db: Session, user: dict, accion: str, detalle: dict | str):
    """
    Guarda un registro de auditoría.
    - accion: etiqueta corta, ej: 'entrada_creada', 'salida_creada', 'ajuste_inventario'
    - detalle: dict (se guarda como JSON) o string.
    """
    if isinstance(detalle, dict):
        detalle_str = json.dumps(detalle, ensure_ascii=False)
    else:
        detalle_str = str(detalle)

    reg = Auditoria(
        negocio=user["negocio"],
        usuario=user["email"],
        accion=accion,
        detalle=detalle_str,
    )
    db.add(reg)
    db.commit()



# ------------ Rutas ------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None}
    )

@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    user = FAKE_USERS.get(email)
    if not user or user["password"] != password:
        # Credenciales inválidas
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Correo o contraseña incorrectos."},
            status_code=401
        )

    # Login OK -> generar cookie firmada
    signed = signer.sign(email.encode("utf-8")).decode("utf-8")
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.set_cookie(
        key="session",
        value=signed,
        httponly=True,
        max_age=60 * 60 * 4  # 4 horas
    )
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response


# ============================
#     DASHBOARD
# ============================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    hoy = date.today()

    # ============================
    # 1) Productos del negocio
    # ============================
    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"])
        .order_by(Producto.nombre.asc())
        .all()
    )
    productos_by_name = {p.nombre.lower(): p for p in productos}
    total_skus = len(productos)

    # ============================
    # 2) Movimientos (todos)
    # ============================
    movimientos_all = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        .all()
    )

    # Totales por producto y lotes para vencimiento (FEFO simplificado por producto)
    totales_producto = {}        # producto -> qty total
    lotes_por_producto = {}      # producto -> lista {"fv": date|None, "qty": int}

    for mov in movimientos_all:
        prod_name = mov.producto
        prod_key = prod_name.lower()

        qty = mov.cantidad or 0
        # misma lógica que en stock: salidas/ajustes negativos restan
        if mov.tipo == "salida" or (mov.tipo == "ajuste" and qty < 0):
            signed_delta = -abs(qty)
        else:
            signed_delta = abs(qty)

        # total por producto
        totales_producto[prod_key] = totales_producto.get(prod_key, 0) + signed_delta

        # lotes por producto (para vencimiento)
        lotes = lotes_por_producto.setdefault(prod_name, [])
        if signed_delta > 0:
            fv = mov.fecha_vencimiento
            lotes.append({"fv": fv, "qty": signed_delta})
        elif signed_delta < 0:
            qty_to_remove = -signed_delta
            # FEFO por producto (independiente del slot)
            lotes.sort(
                key=lambda l: (
                    l["fv"] is None,
                    l["fv"] or date(9999, 12, 31)
                )
            )
            for lote in lotes:
                if qty_to_remove <= 0:
                    break
                disp = lote["qty"]
                if disp <= 0:
                    continue
                usar = min(disp, qty_to_remove)
                lote["qty"] -= usar
                qty_to_remove -= usar
            lotes[:] = [l for l in lotes if l["qty"] > 0]

    # ============================
    # 3) Resumen de estados por producto (min/max)
    # ============================
    resumen_stock = {"Crítico": 0, "OK": 0, "Sobre-stock": 0, "Sin configuración": 0}
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}

    estado_producto = {}  # producto -> estado

    for p in productos:
        key = p.nombre.lower()
        stock_total = totales_producto.get(key, 0)
        stock_min = p.stock_min
        stock_max = p.stock_max

        if stock_min is None and stock_max is None:
            est = "Sin configuración"
        else:
            if stock_min is not None and stock_total < stock_min:
                est = "Crítico"
            elif stock_max is not None and stock_total > stock_max:
                est = "Sobre-stock"
            else:
                est = "OK"

        prev = estado_producto.get(p.nombre)
        if prev is None or prioridad[est] > prioridad.get(prev, 0):
            estado_producto[p.nombre] = est

    for est in estado_producto.values():
        if est in resumen_stock:
            resumen_stock[est] += 1

    # ============================
    # 4) Resumen de vencimientos por producto
    # ============================
    resumen_venc = {
        "Vencido": 0,
        "<7": 0,
        "<15": 0,
        "<30": 0,
        "<60": 0,
        "Normal": 0,
        "Sin fecha": 0,
    }

    for prod_name, lotes in lotes_por_producto.items():
        # tomar la fecha de vencimiento más próxima entre los lotes que quedan
        fv_min = None
        for l in lotes:
            if l["fv"] is not None:
                if fv_min is None or l["fv"] < fv_min:
                    fv_min = l["fv"]
        if fv_min is None:
            resumen_venc["Sin fecha"] += 1
        else:
            dias = (fv_min - hoy).days
            if dias < 0:
                resumen_venc["Vencido"] += 1
            elif dias <= 7:
                resumen_venc["<7"] += 1
            elif dias <= 15:
                resumen_venc["<15"] += 1
            elif dias <= 30:
                resumen_venc["<30"] += 1
            elif dias <= 60:
                resumen_venc["<60"] += 1
            else:
                resumen_venc["Normal"] += 1

    # ============================
    # 5) Total unidades en stock (suma de positivos)
    # ============================
    total_unidades = sum(q for q in totales_producto.values() if q > 0)

    # ============================
    # 6) Últimos movimientos (tabla)
    # ============================
    movimientos_recientes = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(5)
        .all()
    )

    # ============================
    # 7) Datos para gráfico últimos 7 días (entradas vs salidas)
    # ============================
    hace_7_dias = datetime.utcnow() - timedelta(days=7)
    mov_ultimos_7 = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == user["negocio"],
            Movimiento.fecha >= hace_7_dias,
        )
        .order_by(Movimiento.fecha.asc())
        .all()
    )

    serie_por_dia = {}  # fecha (date) -> {"entrada": x, "salida": y}

    for m in mov_ultimos_7:
        dia = m.fecha.date()
        if dia not in serie_por_dia:
            serie_por_dia[dia] = {"entrada": 0, "salida": 0}

        if m.tipo == "salida":
            serie_por_dia[dia]["salida"] += m.cantidad or 0
        else:
            # cualquier no-salida la consideramos entrada (entrada/ajuste/transfer_in)
            serie_por_dia[dia]["entrada"] += m.cantidad or 0

    dias_ordenados = sorted(serie_por_dia.keys())
    chart_labels = [d.strftime("%d-%m") for d in dias_ordenados]
    chart_entradas = [serie_por_dia[d]["entrada"] for d in dias_ordenados]
    chart_salidas = [serie_por_dia[d]["salida"] for d in dias_ordenados]

    # Pasamos las listas como JSON para Chart.js
    chart_data = {
        "labels": chart_labels,
        "entradas": chart_entradas,
        "salidas": chart_salidas,
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_skus": total_skus,
            "total_unidades": total_unidades,
            "resumen_stock": resumen_stock,
            "resumen_venc": resumen_venc,
            "movimientos_recientes": movimientos_recientes,
            "chart_data_json": json.dumps(chart_data),
        }
    )

# ============================
#     ZONAS
# ============================

@app.get("/zonas", response_class=HTMLResponse)
async def zonas_list(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zonas = (
        db.query(Zona)
        .filter(Zona.negocio == user["negocio"])
        .order_by(Zona.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "zonas.html",
        {
            "request": request,
            "user": user,
            "zonas": zonas,
        }
    )

@app.get("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_form(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    return templates.TemplateResponse(
        "zona_nueva.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "nombre": "",
        }
    )


@app.post("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_submit(
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()


    if not nombre:
        return templates.TemplateResponse(
            "zona_nueva.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre de la zona no puede estar vacío.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    # opcional: validar sigla vacía o no
    if not sigla:
        sigla = nombre[:1].upper()

    # Validar que no exista ya la misma zona en ese negocio (case-insensitive)
    existe = (
        db.query(Zona)
        .filter(
            Zona.negocio == user["negocio"],
            func.lower(Zona.nombre) == nombre.lower()
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "zona_nueva.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe una zona con el nombre '{nombre}'.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    zona = Zona(
        negocio=user["negocio"],
        nombre=nombre,
        sigla=sigla,
    )
    db.add(zona)
    db.commit()
    db.refresh(zona)

    print(">>> NUEVA ZONA:", zona.id, zona.nombre, zona.sigla)

    return RedirectResponse(url="/zonas", status_code=302)


# ============================
#     UBICACIONES
# ============================

@app.get("/zonas/{zona_id}/ubicaciones", response_class=HTMLResponse)
async def ubicaciones_list(
    zona_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    ubicaciones = (
        db.query(Ubicacion)
        .filter(Ubicacion.zona_id == zona.id)
        .order_by(Ubicacion.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "ubicaciones.html",
        {
            "request": request,
            "user": user,
            "zona": zona,
            "ubicaciones": ubicaciones,
        }
    )


@app.get("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_form(
    zona_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "ubicacion_nueva.html",
        {
            "request": request,
            "user": user,
            "zona": zona,
            "error": None,
            "nombre": "",
        }
    )


@app.post("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_submit(
    zona_id: int,
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()


    if not nombre:
        return templates.TemplateResponse(
            "ubicacion_nueva.html",
            {
                "request": request,
                "user": user,
                "zona": zona,
                "error": "El nombre de la ubicación no puede estar vacío.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    
    if not sigla:
        # ejemplo: Repisa A -> RA (tomas primera letra de cada palabra)
        partes = nombre.split()
        sigla = "".join(p[0] for p in partes).upper()

    existe = (
        db.query(Ubicacion)
        .filter(
            Ubicacion.zona_id == zona.id,
            func.lower(Ubicacion.nombre) == nombre.lower()
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "ubicacion_nueva.html",
            {
                "request": request,
                "user": user,
                "zona": zona,
                "error": f"Ya existe una ubicación '{nombre}' en esta zona.",
                "nombre": nombre,
                "sigla": sigla,
            },
            status_code=400,
        )

    ubicacion = Ubicacion(
        zona_id=zona.id,
        nombre=nombre,
        sigla=sigla,
    )
    db.add(ubicacion)
    db.commit()
    db.refresh(ubicacion)

    print(">>> NUEVA ZONA:", zona.id, zona.nombre, zona.sigla)

    return RedirectResponse(
        url=f"/zonas/{zona.id}/ubicaciones",
        status_code=302
    )



# ============================
#     SLOTS
# ============================

@app.get("/ubicaciones/{ubicacion_id}/slots", response_class=HTMLResponse)
async def slots_list(ubicacion_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    slots = (
        db.query(Slot)
        .filter(Slot.ubicacion_id == ubicacion.id)
        .order_by(Slot.codigo.asc())
        .all()
    )

    return templates.TemplateResponse(
        "slots.html",
        {
            "request": request,
            "user": user,
            "ubicacion": ubicacion,
            "slots": slots,
        }
    )

@app.get("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_form(ubicacion_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "slot_nuevo.html",
        {
            "request": request,
            "user": user,
            "ubicacion": ubicacion,
            "error": None,
            "codigo": "",
            "capacidad": "",
        }
    )


@app.post("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_submit(
    ubicacion_id: int,
    request: Request,
    codigo: str = Form(...),
    capacidad: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    codigo = (codigo or "").strip().upper()
    capacidad_str = (capacidad or "").strip()

    if not codigo:
        return templates.TemplateResponse(
            "slot_nuevo.html",
            {
                "request": request,
                "user": user,
                "ubicacion": ubicacion,
                "error": "El código del slot no puede estar vacío.",
                "codigo": codigo,
                "capacidad": capacidad_str,
            },
            status_code=400,
        )

    existe = (
        db.query(Slot)
        .filter(
            Slot.ubicacion_id == ubicacion.id,
            func.lower(Slot.codigo) == codigo.lower()
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "slot_nuevo.html",
            {
                "request": request,
                "user": user,
                "ubicacion": ubicacion,
                "error": f"Ya existe un slot '{codigo}' en esta ubicación.",
                "codigo": codigo,
                "capacidad": capacidad_str,
            },
            status_code=400,
        )

    capacidad_int = None
    if capacidad_str.isdigit():
        capacidad_int = int(capacidad_str)

    # 👇 aquí usamos las siglas que ya están en la BD
    zona_sigla = (ubicacion.zona.sigla or ubicacion.zona.nombre[:1]).upper()
    ubic_sigla = (ubicacion.sigla or "".join(p[0] for p in ubicacion.nombre.split())).upper()
    codigo_full = f"{zona_sigla}-{ubic_sigla}-{codigo}"

    slot = Slot(
        ubicacion_id=ubicacion.id,
        codigo=codigo,
        capacidad=capacidad_int,
        codigo_full=codigo_full,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)

    print(f">>> NUEVO SLOT: {slot.codigo_full}")

    return RedirectResponse(
        url=f"/ubicaciones/{ubicacion.id}/slots",
        status_code=302
    )



def get_slots_negocio(db: Session, negocio: str):
    """
    Devuelve todos los slots del negocio con información de zona y ubicación.
    """
    slots = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Zona.negocio == negocio)
        .order_by(Zona.nombre.asc(), Ubicacion.nombre.asc(), Slot.codigo.asc())
        .all()
    )
    return slots



# ============================
#     PRODUCTOS
# ============================

@app.get("/productos", response_class=HTMLResponse)
async def productos_list(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"])
        .order_by(Producto.nombre.asc())
        .all()
    )

    return templates.TemplateResponse(
        "productos.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
        }
    )

@app.get("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_form(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    return templates.TemplateResponse(
        "producto_nuevo.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "nombre": "",
            "unidad": "unidad",
        }
    )


@app.post("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_submit(
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    nombre = (nombre or "").strip()
    unidad = (unidad or "").strip() or "unidad"

    stock_min_str = (stock_min or "").strip()
    stock_max_str = (stock_max or "").strip()

    stock_min_val = int(stock_min_str) if stock_min_str.isdigit() else None
    stock_max_val = int(stock_max_str) if stock_max_str.isdigit() else None

    if not nombre:
        # adapta a tu template actual
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre del producto no puede estar vacío.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
            },
            status_code=400,
        )

    # Validar que no exista ya el mismo nombre para el mismo negocio
    existe = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            func.lower(Producto.nombre) == nombre.lower()
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe un producto con el nombre '{nombre}'.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
            },
            status_code=400,
        )

    producto = Producto(
        negocio=user["negocio"],
        nombre=nombre,
        unidad=unidad,
        stock_min=stock_min_val,
        stock_max=stock_max_val,
    )
    db.add(producto)
    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db,
        user,
        accion="producto_creado",
        detalle={
            "producto_id": producto.id,
            "nombre": producto.nombre,
            "unidad": producto.unidad,
            "stock_min": producto.stock_min,
            "stock_max": producto.stock_max,
        },
    )


    print(">>> NUEVO PRODUCTO:", producto.nombre, "min:", producto.stock_min, "max:", producto.stock_max)

    return RedirectResponse(url="/productos", status_code=302)


@app.get("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_form(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio == user["negocio"],
        )
        .first()
    )
    if not producto:
        return RedirectResponse(url="/productos", status_code=302)

    return templates.TemplateResponse(
        "producto_editar.html",
        {
            "request": request,
            "user": user,
            "error": None,
            "producto": producto,
        }
    )



@app.post("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_submit(
    producto_id: int,
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio == user["negocio"],
        )
        .first()
    )
    if not producto:
        return RedirectResponse(url="/productos", status_code=302)

    nombre = (nombre or "").strip()
    unidad = (unidad or "").strip() or "unidad"
    stock_min_str = (stock_min or "").strip()
    stock_max_str = (stock_max or "").strip()

    stock_min_val = int(stock_min_str) if stock_min_str.isdigit() else None
    stock_max_val = int(stock_max_str) if stock_max_str.isdigit() else None

    if not nombre:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre del producto no puede estar vacío.",
                "producto": producto,
            },
            status_code=400,
        )

    # Validar nombre único dentro del negocio (excluyendo el mismo producto)
    existe = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            func.lower(Producto.nombre) == nombre.lower(),
            Producto.id != producto.id,
        )
        .first()
    )
    if existe:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": f"Ya existe otro producto con el nombre '{nombre}'.",
                "producto": producto,
            },
            status_code=400,
        )

    # Guardar cambios
    producto.nombre = nombre
    producto.unidad = unidad
    producto.stock_min = stock_min_val
    producto.stock_max = stock_max_val

    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db,
        user,
        accion="producto_editado",
        detalle={
            "producto_id": producto.id,
            "nombre": producto.nombre,
            "unidad": producto.unidad,
            "stock_min": producto.stock_min,
            "stock_max": producto.stock_max,
        },
    )

    return RedirectResponse(url="/productos", status_code=302)



@app.post("/productos/{producto_id}/toggle-estado")
async def producto_toggle_estado(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio == user["negocio"],
        )
        .first()
    )
    if not producto:
        return RedirectResponse(url="/productos", status_code=302)

    estado_anterior = producto.activo or 0
    producto.activo = 0 if estado_anterior == 1 else 1

    db.commit()
    db.refresh(producto)

    registrar_auditoria(
        db,
        user,
        accion="producto_toggle_estado",
        detalle={
            "producto_id": producto.id,
            "nombre": producto.nombre,
            "nuevo_estado": "activo" if producto.activo == 1 else "inactivo",
        },
    )

    return RedirectResponse(url="/productos", status_code=302)



# ============================
#     MOVIMIENTOS
# ============================

from datetime import datetime

@app.get("/movimientos", response_class=HTMLResponse)
async def movimientos_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    params = request.query_params

    fecha_desde_str = params.get("desde", "")
    fecha_hasta_str = params.get("hasta", "")
    tipo_filtro = params.get("tipo", "")
    producto_filtro = params.get("producto", "")
    usuario_filtro = params.get("usuario", "")

    query = db.query(Movimiento).filter(Movimiento.negocio == user["negocio"])

    # Filtro por fecha desde
    if fecha_desde_str:
        try:
            dt_desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d")
            query = query.filter(Movimiento.fecha >= dt_desde)
        except ValueError:
            pass

    # Filtro por fecha hasta (inclusive día completo)
    if fecha_hasta_str:
        try:
            dt_hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d")
            dt_hasta_fin = dt_hasta.replace(hour=23, minute=59, second=59)
            query = query.filter(Movimiento.fecha <= dt_hasta_fin)
        except ValueError:
            pass

    # Filtro por tipo
    if tipo_filtro:
        query = query.filter(Movimiento.tipo == tipo_filtro)

    # Filtro por producto (contiene, case-insensitive)
    if producto_filtro:
        query = query.filter(
            func.lower(Movimiento.producto).like(f"%{producto_filtro.lower()}%")
        )

    # Filtro por usuario (contiene, case-insensitive)
    if usuario_filtro:
        query = query.filter(
            func.lower(Movimiento.usuario).like(f"%{usuario_filtro.lower()}%")
        )

    # Orden más reciente primero
    movimientos = (
        query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(500)  # seguridad básica para no reventar la tabla
        .all()
    )

    # Para combos de filtro (selects)
    productos_distintos = (
        db.query(Movimiento.producto)
        .filter(Movimiento.negocio == user["negocio"])
        .distinct()
        .order_by(Movimiento.producto.asc())
        .all()
    )
    usuarios_distintos = (
        db.query(Movimiento.usuario)
        .filter(Movimiento.negocio == user["negocio"])
        .distinct()
        .order_by(Movimiento.usuario.asc())
        .all()
    )

    tipos_distintos = (
        db.query(Movimiento.tipo)
        .filter(Movimiento.negocio == user["negocio"])
        .distinct()
        .order_by(Movimiento.tipo.asc())
        .all()
    )

    # Flatten listas [(x,), (y,)] -> [x, y]
    productos_list = [r[0] for r in productos_distintos if r[0]]
    usuarios_list = [r[0] for r in usuarios_distintos if r[0]]
    tipos_list = [r[0] for r in tipos_distintos if r[0]]

    return templates.TemplateResponse(
        "movimientos.html",
        {
            "request": request,
            "user": user,
            "movimientos": movimientos,
            "productos_list": productos_list,
            "usuarios_list": usuarios_list,
            "tipos_list": tipos_list,
            # valores actuales de filtros (para mantenerlos en el form)
            "f_desde": fecha_desde_str,
            "f_hasta": fecha_hasta_str,
            "f_tipo": tipo_filtro,
            "f_producto": producto_filtro,
            "f_usuario": usuario_filtro,
        }
    )





# ============================
#     MOVIMIENTO DE SALIDA
# ============================

@app.get("/movimientos/salida", response_class=HTMLResponse)
async def salida_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"],
                Producto.activo == 1)
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        return RedirectResponse("/productos/nuevo", status_code=302)

    slots = get_slots_negocio(db, user["negocio"])
    if not slots:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "salida.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_id": "",
        }
    )




@app.post("/movimientos/salida", response_class=HTMLResponse)
async def salida_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    # 🔧 Normalizar entradas
    producto = (producto or "").strip()

    # Buscar slot con su ubicación y zona
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not slot:
        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"],
                    Producto.activo == 1)
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])
        return templates.TemplateResponse(
            "salida.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "La ubicación seleccionada no es válida.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
            },
            status_code=400,
        )

    zona_str = slot.codigo_full

    # 1) Calcular stock actual de ese producto + zona + negocio
    #    (entradas - salidas)
    movimientos = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == user["negocio"],
            func.lower(Movimiento.producto) == producto.lower(),
            Movimiento.zona == zona_str,
        )
        .all()
    )

    entradas = sum(m.cantidad for m in movimientos if m.tipo == "entrada")
    salidas = sum(m.cantidad for m in movimientos if m.tipo == "salida")
    stock_actual = entradas - salidas

    # 2) Verificar si alcanza el stock
    if cantidad > stock_actual:
        # No hay stock suficiente → mostrar error en el mismo formulario
        error_msg = (
            f"No puedes registrar una salida de {cantidad} unidad(es) de '{producto}' "
            f"en {zona_str} porque el stock actual es {stock_actual}."
        )
        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"],
                    Producto.activo == 1)
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])
        return templates.TemplateResponse(
            "salida.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": error_msg,
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
            },
            status_code=400,
        )

    # 3) Registrar salida normal si hay stock suficiente
    movimiento = Movimiento(
        negocio=user["negocio"],
        usuario=user["email"],
        tipo="salida",
        producto=producto,
        cantidad=cantidad,
        zona=zona_str,
        fecha=datetime.utcnow()
    )

    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    registrar_auditoria(
        db,
        user,
        accion="salida_creada",
        detalle={
            "movimiento_id": movimiento.id,
            "producto": producto,
            "cantidad": cantidad,
            "zona": zona_str,
        },
    )

    print(">>> NUEVA SALIDA:", movimiento.id, producto, cantidad, "en", zona_str)

    return RedirectResponse(url="/dashboard", status_code=302)


# ============================
#     MOVIMIENTO DE ENTRADA
# ============================

@app.get("/movimientos/entrada", response_class=HTMLResponse)
async def entrada_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"],
                Producto.activo == 1)
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        return RedirectResponse("/productos/nuevo", status_code=302)

    slots = get_slots_negocio(db, user["negocio"])
    if not slots:
        # Si no hay slots, que vaya a configurar el diseño del almacén
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "entrada.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
        }
    )




@app.post("/movimientos/entrada")
async def entrada_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    fecha_vencimiento: str = Form(""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    producto = (producto or "").strip()

    # Buscar slot con su ubicación y zona
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    if not slot:
        # slot inválido → volver al formulario
        slots = get_slots_negocio(db, user["negocio"])
        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"])
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])
        return templates.TemplateResponse(
            "entrada.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "La ubicación seleccionada no es válida.",
            },
            status_code=400,
        )

    zona_str = slot.codigo_full

    # Parsear fecha de vencimiento (si viene)
    fv_date = None
    fv_str = (fecha_vencimiento or "").strip()
    if fv_str:
        try:
            fv_date = datetime.strptime(fv_str, "%Y-%m-%d").date()
        except ValueError:
            # Si viene mal, simplemente la ignoramos en este MVP
            fv_date = None

    movimiento = Movimiento(
        negocio=user["negocio"],
        usuario=user["email"],
        tipo="entrada",
        producto=producto,
        cantidad=cantidad,
        zona=zona_str,
        fecha=datetime.utcnow(),
        fecha_vencimiento=fv_date,
    )

    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    registrar_auditoria(
        db,
        user,
        accion="entrada_creada",
        detalle={
            "movimiento_id": movimiento.id,
            "producto": producto,
            "cantidad": cantidad,
            "zona": zona_str,
            "fecha_vencimiento": str(fv_date) if fv_date else None,
        },
    )

    print(
        ">>> NUEVA ENTRADA:",
        movimiento.id,
        movimiento.producto,
        movimiento.cantidad,
        "en",
        zona_str,
        "vence:",
        movimiento.fecha_vencimiento,
    )

    return RedirectResponse(url="/dashboard", status_code=302)

# ============================
#     MOVIMIENTO DE TRANSFERENCIA
# ============================

@app.get("/transferencia", response_class=HTMLResponse)
async def transferencia_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"],
                 Producto.activo == 1)
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        return RedirectResponse("/productos/nuevo", status_code=302)

    slots = get_slots_negocio(db, user["negocio"])
    if not slots:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "transferencia.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_origen_id": "",
            "slot_destino_id": "",
        }
    )

@app.post("/transferencia", response_class=HTMLResponse)
async def transferencia_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_origen_id: int = Form(...),
    slot_destino_id: int = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    producto = (producto or "").strip()

    # Si el origen y destino son el mismo, no tiene sentido la transferencia
    if slot_origen_id == slot_destino_id:
        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"],
                    Producto.activo == 1)
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])
        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "El slot de origen y el de destino no pueden ser el mismo.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    # Buscar slots de origen y destino
    slot_origen = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_origen_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )
    slot_destino = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_destino_id,
            Zona.negocio == user["negocio"]
        )
        .first()
    )

    if not slot_origen or not slot_destino:
        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"],
                    Producto.activo == 1)
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])
        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "Alguno de los slots seleccionados no es válido.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    zona_origen_str = slot_origen.codigo_full
    zona_destino_str = slot_destino.codigo_full

    # 1) Calcular stock actual en el slot de origen para ese producto
    movimientos_origen = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == user["negocio"],
            func.lower(Movimiento.producto) == producto.lower(),
            Movimiento.zona == zona_origen_str,
        )
        .all()
    )

    entradas = sum(m.cantidad for m in movimientos_origen if m.tipo == "entrada")
    salidas = sum(m.cantidad for m in movimientos_origen if m.tipo == "salida")
    stock_origen = entradas - salidas

    if cantidad > stock_origen:
        error_msg = (
            f"No puedes transferir {cantidad} unidad(es) de '{producto}' "
            f"desde {zona_origen_str} porque el stock actual es {stock_origen}."
        )

        productos = (
            db.query(Producto)
            .filter(Producto.negocio == user["negocio"],
                    Producto.activo == 1)
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, user["negocio"])

        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": error_msg,
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    # 2) Crear salida en origen
    mov_salida = Movimiento(
        negocio=user["negocio"],
        usuario=user["email"],
        tipo="salida",
        producto=producto,
        cantidad=cantidad,
        zona=zona_origen_str,
        fecha=datetime.utcnow()
    )

    # 3) Crear entrada en destino
    mov_entrada = Movimiento(
        negocio=user["negocio"],
        usuario=user["email"],
        tipo="entrada",
        producto=producto,
        cantidad=cantidad,
        zona=zona_destino_str,
        fecha=datetime.utcnow()
    )

    db.add(mov_salida)
    db.add(mov_entrada)
    db.commit()

    print(
        f">>> TRANSFERENCIA: {cantidad} x '{producto}' "
        f"de {zona_origen_str} a {zona_destino_str} "
        f"(mov_salida={mov_salida.id}, mov_entrada={mov_entrada.id})"
    )

    # Te llevo a stock para ver el efecto
    return RedirectResponse(url="/stock", status_code=302)



# ============================
#           STOCK
# ============================



@app.get("/stock", response_class=HTMLResponse)
async def stock_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    hoy = date.today()

    # ============================
    # Filtros desde la URL (GET)
    # ============================
    params = request.query_params
    f_producto = (params.get("producto", "") or "").strip()
    f_zona = (params.get("zona", "") or "").strip()
    f_estado = (params.get("estado", "") or "").strip()
    f_vencimiento = (params.get("vencimiento", "") or "").strip()

    # ============================
    # 1) Productos del negocio
    # ============================
    productos = (
        db.query(Producto)
        .filter(Producto.negocio == user["negocio"])
        .order_by(Producto.nombre.asc())
        .all()
    )
    productos_by_name = {p.nombre.lower(): p for p in productos}

    # ============================
    # 2) Movimientos con join Slot/Ubic/Zona (orden FEFO)
    # ============================
    movimientos = (
        db.query(Movimiento, Slot, Ubicacion, Zona)
        .outerjoin(Slot, Movimiento.zona == Slot.codigo_full)
        .outerjoin(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .outerjoin(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        .all()
    )

    totales_producto = {}        # prod_key -> qty total
    stock_por_slot = {}          # (producto, zona_str) -> info
    lotes_por_slot = {}          # (producto, zona_str) -> lista {"fv": date|None, "qty": int}

    for mov, slot, ubic, zona in movimientos:
        prod_name = mov.producto
        prod_key = prod_name.lower()
        zona_str = mov.zona  # D-RA-C1, etc.

        qty = mov.cantidad or 0
        # salidas/ajustes negativos restan, el resto suma
        if mov.tipo == "salida" or (mov.tipo == "ajuste" and qty < 0):
            signed_delta = -abs(qty)
        else:
            signed_delta = abs(qty)

        # Totales por producto
        totales_producto[prod_key] = totales_producto.get(prod_key, 0) + signed_delta

        # Stock por slot
        slot_key = (prod_name, zona_str)
        if slot_key not in stock_por_slot:
            stock_por_slot[slot_key] = {
                "producto": prod_name,
                "zona_str": zona_str,
                "cantidad": 0,
                "slot": slot,
                "ubic": ubic,
                "zona": zona,
            }
        info = stock_por_slot[slot_key]
        info["cantidad"] += signed_delta
        if slot is not None:
            info["slot"] = slot
        if ubic is not None:
            info["ubic"] = ubic
        if zona is not None:
            info["zona"] = zona

        # Lotes por vencimiento (FEFO simplificado)
        lotes = lotes_por_slot.setdefault(slot_key, [])
        if signed_delta > 0:
            fv = mov.fecha_vencimiento
            lotes.append({"fv": fv, "qty": signed_delta})
        elif signed_delta < 0:
            qty_to_remove = -signed_delta
            lotes.sort(
                key=lambda l: (
                    l["fv"] is None,
                    l["fv"] or date(9999, 12, 31)
                )
            )
            for lote in lotes:
                if qty_to_remove <= 0:
                    break
                disp = lote["qty"]
                if disp <= 0:
                    continue
                usar = min(disp, qty_to_remove)
                lote["qty"] -= usar
                qty_to_remove -= usar
            lotes[:] = [l for l in lotes if l["qty"] > 0]

    # ============================
    # 3) Construir filas base (todas)
    # ============================
    filas_all = []

    for (producto_nombre, zona_str), info in stock_por_slot.items():
        cantidad_slot = info["cantidad"]
        if cantidad_slot == 0:
            continue

        prod_key = producto_nombre.lower()
        prod = productos_by_name.get(prod_key)

        stock_total = totales_producto.get(prod_key, 0)
        stock_min = prod.stock_min if prod else None
        stock_max = prod.stock_max if prod else None

        # Estado por min/max (a nivel producto total)
        estado = "Sin configuración"
        estado_css = "bg-slate-200 text-slate-700"

        if stock_min is None and stock_max is None:
            estado = "Sin configuración"
        else:
            if stock_min is not None and stock_total < stock_min:
                estado = "Crítico"
                estado_css = "bg-red-100 text-red-700 border border-red-200"
            elif stock_max is not None and stock_total > stock_max:
                estado = "Sobre-stock"
                estado_css = "bg-amber-100 text-amber-700 border border-amber-200"
            else:
                estado = "OK"
                estado_css = "bg-emerald-100 text-emerald-700 border border-emerald-200"

        slot = info["slot"]
        ubic = info["ubic"]
        zona_obj = info["zona"]

        capacidad = slot.capacidad if slot is not None else None
        ocupacion_pct = None
        if capacidad and capacidad > 0:
            ocupacion_pct = round(cantidad_slot * 100 / capacidad, 1)

        # Estado de vencimiento según lotes restantes
        lotes = lotes_por_slot.get((producto_nombre, zona_str), [])
        fv_min = None
        for l in lotes:
            if l["fv"] is not None:
                if fv_min is None or l["fv"] < fv_min:
                    fv_min = l["fv"]

        venc_estado = "Sin fecha"
        venc_css = "bg-slate-100 text-slate-700 border border-slate-200"
        venc_dias = None

        if fv_min is not None:
            dias_restantes = (fv_min - hoy).days
            venc_dias = dias_restantes

            if dias_restantes < 0:
                venc_estado = "Vencido"
                venc_css = "bg-red-100 text-red-700 border border-red-200"
            elif dias_restantes <= 7:
                venc_estado = "Por vencer <7 días"
                venc_css = "bg-orange-100 text-orange-700 border border-orange-200"
            elif dias_restantes <= 15:
                venc_estado = "Por vencer <15 días"
                venc_css = "bg-amber-100 text-amber-700 border border-amber-200"
            elif dias_restantes <= 30:
                venc_estado = "Por vencer <30 días"
                venc_css = "bg-yellow-100 text-yellow-700 border border-yellow-200"
            elif dias_restantes <= 60:
                venc_estado = "Por vencer <60 días"
                venc_css = "bg-lime-100 text-lime-700 border border-lime-200"
            else:
                venc_estado = "Normal"
                venc_css = "bg-emerald-100 text-emerald-700 border border-emerald-200"

        filas_all.append({
            "producto": producto_nombre,
            "unidad": prod.unidad if prod else "unidad",
            "zona_nombre": zona_obj.nombre if zona_obj is not None else "-",
            "ubicacion_nombre": ubic.nombre if ubic is not None else "-",
            "codigo_full": slot.codigo_full if slot is not None else zona_str,
            "cantidad": cantidad_slot,
            "stock_total": stock_total,
            "stock_min": stock_min,
            "stock_max": stock_max,
            "estado": estado,
            "estado_css": estado_css,
            "capacidad": capacidad,
            "ocupacion_pct": ocupacion_pct,
            "vencimiento_fecha": fv_min,
            "vencimiento_dias": venc_dias,
            "vencimiento_estado": venc_estado,
            "vencimiento_css": venc_css,
        })

    # Productos sin stock pero con reglas configuradas
    for p in productos:
        key = p.nombre.lower()
        if totales_producto.get(key, 0) == 0:
            stock_min = p.stock_min
            stock_max = p.stock_max
            stock_total = 0

            if stock_min is None and stock_max is None:
                estado = "Sin configuración"
                estado_css = "bg-slate-200 text-slate-700"
            else:
                if stock_min is not None and stock_total < stock_min:
                    estado = "Crítico"
                    estado_css = "bg-red-100 text-red-700 border border-red-200"
                elif stock_max is not None and stock_total > stock_max:
                    estado = "Sobre-stock"
                    estado_css = "bg-amber-100 text-amber-700 border border-amber-200"
                else:
                    estado = "OK"
                    estado_css = "bg-emerald-100 text-emerald-700 border border-emerald-200"

            filas_all.append({
                "producto": p.nombre,
                "unidad": p.unidad,
                "zona_nombre": "-",
                "ubicacion_nombre": "-",
                "codigo_full": "-",
                "cantidad": 0,
                "stock_total": stock_total,
                "stock_min": stock_min,
                "stock_max": stock_max,
                "estado": estado,
                "estado_css": estado_css,
                "capacidad": None,
                "ocupacion_pct": None,
                "vencimiento_fecha": None,
                "vencimiento_dias": None,
                "vencimiento_estado": "Sin fecha",
                "vencimiento_css": "bg-slate-100 text-slate-700 border border-slate-200",
            })

    # ============================
    # 4) Opciones para selects (de todas las filas)
    # ============================
    zonas_list = sorted({
        r["zona_nombre"] for r in filas_all
        if r["zona_nombre"] and r["zona_nombre"] != "-"
    })
    estados_list = sorted({r["estado"] for r in filas_all})
    venc_list = sorted({r["vencimiento_estado"] for r in filas_all})

    # ============================
    # 5) Aplicar filtros sobre filas_all
    # ============================
    filas_filtradas = []

    for r in filas_all:
        if f_producto and f_producto.lower() not in r["producto"].lower():
            continue
        if f_zona and r["zona_nombre"] != f_zona:
            continue
        if f_estado and r["estado"] != f_estado:
            continue
        if f_vencimiento and r["vencimiento_estado"] != f_vencimiento:
            continue
        filas_filtradas.append(r)

    # ============================
    # 6) Ordenar filas filtradas
    # ============================
    filas_filtradas.sort(
        key=lambda r: (
            r["zona_nombre"] or "",
            r["ubicacion_nombre"] or "",
            r["codigo_full"] or "",
            r["producto"].lower(),
        )
    )

    # ============================
    # 7) Resumen de estados (sólo filas filtradas)
    # ============================
    resumen_estados = {"Crítico": 0, "OK": 0, "Sobre-stock": 0, "Sin configuración": 0}
    estado_producto = {}
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}

    for r in filas_filtradas:
        prod = r["producto"]
        est = r["estado"]
        prev = estado_producto.get(prod)
        if prev is None or prioridad.get(est, 0) > prioridad.get(prev, 0):
            estado_producto[prod] = est

    for est in estado_producto.values():
        if est in resumen_estados:
            resumen_estados[est] += 1

    return templates.TemplateResponse(
        "stock.html",
        {
            "request": request,
            "user": user,
            "filas": filas_filtradas,
            "resumen": resumen_estados,
            "zonas_list": zonas_list,
            "estados_list": estados_list,
            "venc_list": venc_list,
            # filtros actuales para mantener valores en el form
            "f_producto": f_producto,
            "f_zona": f_zona,
            "f_estado": f_estado,
            "f_vencimiento": f_vencimiento,
        }
    )

# ============================
#      INVENTARIO / CONTEO
# ============================

@app.get("/inventario", response_class=HTMLResponse)
async def inventario_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    # 1. Traer movimientos del negocio
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .all()
    )

    # 2. Calcular stock teórico por (producto_norm, zona)
    resumen = {}

    for m in movimientos:
        nombre_original = (m.producto or "").strip()
        nombre_norm = nombre_original.lower()
        zona_norm = (m.zona or "").strip()

        key = (nombre_norm, zona_norm)
        if key not in resumen:
            resumen[key] = {
                "producto_display": nombre_original,
                "zona": zona_norm,
                "entradas": 0,
                "salidas": 0,
            }

        if m.tipo == "entrada":
            resumen[key]["entradas"] += m.cantidad
        elif m.tipo == "salida":
            resumen[key]["salidas"] += m.cantidad

    stock_items = []
    for key, data in resumen.items():
        stock_actual = data["entradas"] - data["salidas"]
        stock_items.append({
            "producto": data["producto_display"],
            "zona": data["zona"],
            "stock_actual": stock_actual,
        })

    # Ordenamos por zona y nombre
    stock_items.sort(key=lambda x: (x["zona"], x["producto"]))

    return templates.TemplateResponse(
        "inventario.html",
        {
            "request": request,
            "user": user,
            "stock_items": stock_items,
        }
    )


@app.post("/inventario", response_class=HTMLResponse)
async def inventario_submit(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    form = await request.form()
    total_items = int(form.get("total_items", 0))

    # 1. Recalcular stock teórico igual que en el GET
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .all()
    )

    resumen = {}
    for m in movimientos:
        nombre_original = (m.producto or "").strip()
        nombre_norm = nombre_original.lower()
        zona_norm = (m.zona or "").strip()

        key = (nombre_norm, zona_norm)
        if key not in resumen:
            resumen[key] = {
                "producto_display": nombre_original,
                "zona": zona_norm,
                "entradas": 0,
                "salidas": 0,
            }

        if m.tipo == "entrada":
            resumen[key]["entradas"] += m.cantidad
        elif m.tipo == "salida":
            resumen[key]["salidas"] += m.cantidad

    # 2. Procesar conteos y generar ajustes
    ajustes_realizados = 0

    for i in range(total_items):
        producto = (form.get(f"producto_{i}") or "").strip()
        zona = (form.get(f"zona_{i}") or "").strip()
        conteo_str = form.get(f"conteo_{i}") or ""
        try:
            conteo = int(conteo_str)
        except ValueError:
            conteo = 0

        if not producto:
            continue

        key_norm = (producto.lower(), zona)
        data = resumen.get(key_norm)

        stock_teorico = 0
        if data is not None:
            stock_teorico = data["entradas"] - data["salidas"]

        diff = conteo - stock_teorico

        if diff == 0:
            continue  # no hay ajuste

        # Si diff > 0 → faltaba stock en el sistema → registramos una "entrada"
        # Si diff < 0 → sobraba stock en el sistema → registramos una "salida"
        tipo_mov = "entrada" if diff > 0 else "salida"
        cantidad_ajuste = abs(diff)

        movimiento = Movimiento(
            negocio=user["negocio"],
            usuario=user["email"],
            tipo=tipo_mov,
            producto=producto,
            cantidad=cantidad_ajuste,
            zona=zona,
            fecha=datetime.utcnow()
        )
        db.add(movimiento)
        ajustes_realizados += 1

        print(
            f">>> AJUSTE INVENTARIO: {tipo_mov} {cantidad_ajuste} x '{producto}' en {zona} "
            f"(teórico={stock_teorico}, conteo={conteo})"
        )

    if ajustes_realizados > 0:
        db.commit()

    # Luego de ajustar, volvemos al /stock para ver el resultado
    return RedirectResponse(url="/stock", status_code=302)



# ============================
#     HISTORIAL
# ============================

@app.get("/movimientos/historial", response_class=HTMLResponse)
async def movimientos_historial(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    # Traer solo movimientos del negocio del usuario
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.desc())
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(
        "historial.html",
        {
            "request": request,
            "user": user,
            "movimientos": movimientos
        }
    )

# ============================
#      AUDITORIA
# ============================

@app.get("/auditoria", response_class=HTMLResponse)
async def auditoria_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Solo admin y superadmin
    require_role(user, ("admin", "superadmin"))

    registros = (
        db.query(Auditoria)
        .filter(Auditoria.negocio == user["negocio"])
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        "auditoria.html",
        {"request": request, "user": user, "registros": registros},
    )




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("miniWMS:app", host="0.0.0.0", port=8000, reload=True)
