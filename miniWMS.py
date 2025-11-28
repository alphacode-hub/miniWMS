import os
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import TimestampSigner, BadSignature
from pathlib import Path
from datetime import datetime, date, timedelta

from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, func, Date, text, FLOAT
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

import json, bcrypt, secrets

# ============================
#   CONFIGURACIÓN BÁSICA
# ============================

BASE_DIR = Path(__file__).resolve().parent

# Flag de entorno (desarrollo vs producción)
DEBUG = os.getenv("MINIWMS_DEBUG", "true").lower() == "true"

# Base de datos (permite sobreescribir en producción)
SQLALCHEMY_DATABASE_URL = os.getenv(
    "MINIWMS_DB_URL",
    f"sqlite:///{BASE_DIR / 'miniWMS.db'}"
)

# Clave secreta para firmar cookies de sesión
# ⚠️ En producción: export MINIWMS_SECRET_KEY="una-clave-larga-y-unica"
SECRET_KEY = os.getenv("MINIWMS_SECRET_KEY", "dev-secret-change-me")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}  # Necesario para SQLite en FastAPI
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# ============================
#   DICCIONARIO PLANES
# ============================

PLANES_CONFIG = {
    "demo": {
        "max_usuarios": 1,
        "max_productos": 50,
        "max_zonas": 5,
        "max_ubicaciones": 20,
        "max_slots": 200,
        "alertas_habilitadas": False,
        "exportaciones_habilitadas": False,
    },
    "free": {
        "max_usuarios": 2,
        "max_productos": 100,
        "max_zonas": 10,
        "max_ubicaciones": 50,
        "max_slots": 500,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
    "basic": {
        "max_usuarios": 5,
        "max_productos": 500,
        "max_zonas": 50,
        "max_ubicaciones": 300,
        "max_slots": 2000,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
    "pro": {
        "max_usuarios": 20,
        "max_productos": 3000,
        "max_zonas": 200,
        "max_ubicaciones": 2000,
        "max_slots": 20000,
        "alertas_habilitadas": True,
        "exportaciones_habilitadas": True,
    },
}

# ============================
#   APP, TEMPLATES Y FIRMA
# ============================

app = FastAPI(
    title="MiniWMS",
    version="1.0.0",
    debug=DEBUG,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

signer = TimestampSigner(SECRET_KEY)


# =======================
# Seguridad: contraseñas con bcrypt directo
# =======================

BCRYPT_MAX_LENGTH = 72  # bcrypt solo usa los primeros 72 bytes de la contraseña


def hash_password(password: str) -> str:
    """
    Hashea la contraseña en texto plano usando bcrypt.
    Devuelve el hash como string UTF-8.

    Nota:
        bcrypt solo considera los primeros 72 bytes de la contraseña.
        Aquí truncamos explícitamente para evitar sorpresas.
    """
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")
    else:
        password_bytes = password

    password_bytes = password_bytes[:BCRYPT_MAX_LENGTH]

    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifica una contraseña en texto plano contra su hash almacenado.
    """
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
        # Por si el hash almacenado tiene un formato inválido.
        return False
      

# ============================
#   CLASES DEL MODELO
# ============================

class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    negocio = Column(String, index=True, nullable=False)  # nombre del negocio (fantasía)
    usuario = Column(String, index=True, nullable=False)  # email del usuario
    accion = Column(String, index=True, nullable=False)   # etiqueta corta: 'entrada_creada', etc.
    detalle = Column(String, nullable=True)               # JSON o texto


class Negocio(Base):
    __tablename__ = "negocios"

    id = Column(Integer, primary_key=True, index=True)
    nombre_fantasia = Column(String, nullable=False, unique=True, index=True)
    whatsapp_notificaciones = Column(String, nullable=True)
    estado = Column(String, nullable=False, default="activo")  # activo / suspendido
    plan_tipo = Column(String, default="demo", nullable=False)
    plan_fecha_inicio = Column(Date, nullable=True)
    plan_fecha_fin = Column(Date, nullable=True)
    plan_renovacion_cada_meses = Column(Integer, default=1, nullable=False)
    ultimo_acceso = Column(DateTime, nullable=True)

    # Relaciones
    usuarios = relationship(
        "Usuario",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    alertas = relationship(
        "Alerta",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    rol = Column(String, nullable=False, default="operador")  # superadmin / admin / operador
    activo = Column(Integer, nullable=False, default=1)       # 1 = activo, 0 = inactivo
    nombre_mostrado = Column(String, nullable=True)           # opcional, para UI

    negocio = relationship("Negocio", back_populates="usuarios")
    sesiones = relationship(
        "SesionUsuario",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )


class SesionUsuario(Base):
    """
    Control de sesiones activas por usuario.

    - Registro de la sesión actual (token_sesion)
    - Invalidar sesiones anteriores al hacer login nuevo
    """
    __tablename__ = "sesiones_usuario"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    token_sesion = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    activo = Column(Integer, default=1, nullable=False)  # 1 = sesión activa, 0 = invalidada

    usuario = relationship("Usuario", back_populates="sesiones")


class Zona(Base):
    __tablename__ = "zonas"

    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True, nullable=False)  # nombre del negocio
    nombre = Column(String, index=True, nullable=False)
    sigla = Column(String, index=True, nullable=True)

    ubicaciones = relationship(
        "Ubicacion",
        back_populates="zona",
        cascade="all, delete-orphan",
    )


class Ubicacion(Base):
    __tablename__ = "ubicaciones"

    id = Column(Integer, primary_key=True, index=True)
    zona_id = Column(Integer, ForeignKey("zonas.id"), index=True, nullable=False)
    nombre = Column(String, index=True, nullable=False)
    sigla = Column(String, index=True, nullable=True)

    zona = relationship("Zona", back_populates="ubicaciones")
    slots = relationship(
        "Slot",
        back_populates="ubicacion",
        cascade="all, delete-orphan",
    )


class Slot(Base):
    __tablename__ = "slots"

    id = Column(Integer, primary_key=True, index=True)
    ubicacion_id = Column(Integer, ForeignKey("ubicaciones.id"), index=True, nullable=False)
    codigo = Column(String, index=True, nullable=False)      # Ej: C1, C2, C3...
    capacidad = Column(Integer, default=None)                # opcional
    codigo_full = Column(String, index=True, nullable=False) # Ej: Z1-R1-C1

    ubicacion = relationship("Ubicacion", back_populates="slots")


class Producto(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True, nullable=False)   # nombre del negocio
    nombre = Column(String, index=True, nullable=False)
    unidad = Column(String, default="unidad", nullable=False)
    stock_min = Column(Integer, nullable=True)
    stock_max = Column(Integer, nullable=True)
    activo = Column(Integer, default=1, nullable=False)
    costo_unitario = Column(FLOAT, nullable=True)


class Movimiento(Base):
    __tablename__ = "movimientos"

    id = Column(Integer, primary_key=True, index=True)
    negocio = Column(String, index=True, nullable=False)   # nombre del negocio
    usuario = Column(String, index=True, nullable=False)   # email
    tipo = Column(String, index=True, nullable=False)      # entrada / salida / ajuste / etc.
    producto = Column(String, index=True, nullable=False)
    cantidad = Column(Integer, nullable=False)
    zona = Column(String, nullable=False)                  # código_full del slot
    fecha = Column(DateTime, default=datetime.utcnow, nullable=False)
    fecha_vencimiento = Column(Date, nullable=True)
    motivo_salida = Column(String, nullable=True)


class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    tipo = Column(String, nullable=False)      # stock_min, stock_max, vencimiento, etc.
    mensaje = Column(String, nullable=False)
    destino = Column(String, nullable=True)    # futuro: whatsapp, email

    estado = Column(String, nullable=False, default="pendiente")  # pendiente, leida, enviada, error
    fecha_creacion = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    fecha_envio = Column(DateTime, nullable=True)

    origen = Column(String, nullable=True)     # 'entrada', 'salida', 'sistema', etc.
    datos_json = Column(String, nullable=True) # JSON opcional

    negocio = relationship("Negocio", back_populates="alertas")


# ============================
# Seed inicial: superadmin
# ============================

def seed_superadmin():
    """
    Crea un usuario superadmin (tú) si no existe.
    - Crea un negocio 'Global' si no existe.
    - Crea el usuario superadmin ligado a ese negocio.
    """
    db = SessionLocal()
    try:
        email_root = "root@superadmin.cl"  # 👈 puedes cambiarlo si quieres
        existing = db.query(Usuario).filter(Usuario.email == email_root).first()
        if existing:
            return  # ya está creado

        # Crear negocio 'Global'
        negocio_global = Negocio(
            nombre_fantasia="Global",
            whatsapp_notificaciones=None,
            estado="activo",
        )
        db.add(negocio_global)
        db.flush()  # para tener negocio_global.id

        # Crear usuario superadmin
        superadmin = Usuario(
            negocio_id=negocio_global.id,
            email=email_root,
            password_hash=hash_password("12345678"),  # 👈 cambia esta contraseña luego
            rol="superadmin",
            activo=1,
            nombre_mostrado="Superadmin",
        )
        db.add(superadmin)
        db.commit()
        print(f"Superadmin creado: {email_root}")
    except Exception as e:
        db.rollback()
        print(f"[SEED_SUPERADMIN] Error al crear superadmin: {e}")
    finally:
        db.close()


# ============================
# CREAR TABLAS Y SEED INICIAL
# ============================

Base.metadata.create_all(bind=engine)
seed_superadmin()

# ============================
# Dependencia de BD
# ============================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================
# Helpers de sesión (BD)
# ============================

def crear_sesion_db(db: Session, usuario: Usuario) -> str:
    """
    Crea un registro de sesión para el usuario, invalidando sesiones anteriores.
    Devuelve el token de sesión generado.
    """
    # Invalidar sesiones activas anteriores
    db.query(SesionUsuario).filter(
        SesionUsuario.usuario_id == usuario.id,
        SesionUsuario.activo == 1
    ).update({SesionUsuario.activo: 0})

    token_sesion = secrets.token_urlsafe(32)

    nueva_sesion = SesionUsuario(
        usuario_id=usuario.id,
        token_sesion=token_sesion,
        activo=1,
    )
    db.add(nueva_sesion)
    db.commit()

    return token_sesion


def crear_cookie_sesion(response: RedirectResponse, usuario: Usuario, token_sesion: str):
    """
    Crea el payload JSON, lo firma y lo guarda en la cookie 'session'.
    """
    payload = {
        "user_id": usuario.id,
        "email": usuario.email,
        "rol": usuario.rol,
        "negocio_id": usuario.negocio_id,
        "negocio": usuario.negocio.nombre_fantasia if usuario.negocio else None,
        "token_sesion": token_sesion,
        "ts": datetime.utcnow().isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signed = signer.sign(data).decode("utf-8")

    response.set_cookie(
        key="session",
        value=signed,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 4,  # 4 horas
        # secure=True  # activar en producción con HTTPS
    )


def get_current_user(request: Request):
    """
    Lee la cookie, verifica firma y sesión en BD y devuelve un dict con:
    {id, email, negocio, negocio_id, rol} o None si no hay sesión válida.
    """
    cookie = request.cookies.get("session")
    if not cookie:
        return None

    try:
        data = signer.unsign(cookie).decode("utf-8")
        payload = json.loads(data)
    except (BadSignature, json.JSONDecodeError):
        return None

    user_id = payload.get("user_id")
    token_sesion = payload.get("token_sesion")
    if not user_id or not token_sesion:
        return None

    db = SessionLocal()
    try:
        # Verificar que la sesión siga activa
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

        usuario = db.query(Usuario).filter(
            Usuario.id == user_id,
            Usuario.activo == 1,
        ).first()
        if not usuario:
            return None

        # Actualizar last_seen_at
        sesion.last_seen_at = datetime.utcnow()
        db.commit()

        negocio_nombre = (
            usuario.negocio.nombre_fantasia if usuario.negocio else "Global"
        )

        return {
            "id": usuario.id,
            "email": usuario.email,
            "negocio": negocio_nombre,
            "negocio_id": usuario.negocio_id,
            "rol": usuario.rol,
        }
    finally:
        db.close()


# ============================
# Helpers de autorización
# ============================

def is_superadmin(user: dict) -> bool:
    return user.get("rol") == "superadmin"


def require_superadmin(user: dict):
    """
    Atajo para exigir rol superadmin.
    """
    if not user or user.get("rol") != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Solo superadmin puede acceder a esta sección",
        )


def require_role(user: dict, allowed_roles: tuple[str, ...]):
    """
    Lanza 403 si el usuario no tiene un rol permitido.
    allowed_roles: ej. ("admin", "superadmin")
    """
    if user.get("rol") not in allowed_roles:
        raise HTTPException(status_code=403, detail="No autorizado para esta acción")


def login_required(request: Request):
    """
    Helper para vistas HTML:
    - Devuelve el dict user si hay sesión.
    - Si no hay sesión, devuelve un RedirectResponse al login.
    
    ⚠️ Importante:
    No usar junto con require_superadmin(user) sin verificar que
    user no sea un RedirectResponse. En vistas nuevas, mejor usar
    get_current_user + if not user: RedirectResponse(...)
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return user


# ============================
# Auditoría
# ============================

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


# ============================
# Límites por plan
# ============================

def check_plan_limit(db: Session, negocio_id: int, recurso: str):
    """
    Valida que un negocio no exceda los límites de su plan.
    recurso puede ser: usuarios, productos, zonas, ubicaciones, slots.
    """

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=400, detail="Negocio no encontrado.")

    plan = negocio.plan_tipo
    conf = PLANES_CONFIG.get(plan)
    if not conf:
        raise HTTPException(status_code=400, detail="Plan no válido.")

    limite = conf.get(f"max_{recurso}")
    if limite is None:
        return  # sin límite

    # Conteo según recurso
    if recurso == "usuarios":
        count = db.query(Usuario).filter(Usuario.negocio_id == negocio.id).count()
    elif recurso == "productos":
        count = db.query(Producto).filter(Producto.negocio == negocio.nombre_fantasia).count()
    elif recurso == "zonas":
        count = db.query(Zona).filter(Zona.negocio == negocio.nombre_fantasia).count()
    elif recurso == "ubicaciones":
        count = (
            db.query(Ubicacion)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(Zona.negocio == negocio.nombre_fantasia)
            .count()
        )
    elif recurso == "slots":
        count = (
            db.query(Slot)
            .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
            .join(Zona, Ubicacion.zona_id == Zona.id)
            .filter(Zona.negocio == negocio.nombre_fantasia)
            .count()
        )
    else:
        raise HTTPException(status_code=400, detail="Recurso desconocido.")

    if count >= limite:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Has alcanzado el máximo de {recurso} para tu plan "
                f"({count}/{limite}). Contáctanos para ampliar tu capacidad."
            ),
        )


# ============================
# Alertas internas
# ============================

def crear_alerta_interna(
    db: Session,
    negocio_id: int,
    tipo: str,
    mensaje: str,
    origen: str = "sistema",
    datos: dict | None = None,
    destino: str | None = None,
):
    """
    Crea una alerta interna para un negocio.
    - Evita duplicar EXACTAMENTE el mismo mensaje en las últimas 24h.
    - Guarda detalles opcionales en datos_json (como JSON).
    """
    ahora = datetime.utcnow()
    hace_24h = ahora - timedelta(hours=24)

    # Evitar spam: misma alerta, mismo mensaje en menos de 24h
    alerta_existente = (
        db.query(Alerta)
        .filter(
            Alerta.negocio_id == negocio_id,
            Alerta.tipo == tipo,
            Alerta.mensaje == mensaje,
            Alerta.fecha_creacion >= hace_24h,
        )
        .first()
    )

    if alerta_existente:
        return alerta_existente

    datos_json = json.dumps(datos, ensure_ascii=False) if datos else None

    alerta = Alerta(
        negocio_id=negocio_id,
        tipo=tipo,
        mensaje=mensaje,
        destino=destino,
        estado="pendiente",  # flujo interno: pendiente → leida / enviada
        fecha_creacion=ahora,
        fecha_envio=None,
        origen=origen,
        datos_json=datos_json,
    )
    db.add(alerta)
    db.commit()
    db.refresh(alerta)
    return alerta


# ============================
# ALERTAS (reglas de negocio)
# ============================

def evaluar_alertas_stock(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
):
    """
    Evalúa si el producto está en stock crítico o sobre-stock
    y genera alertas internas en base a stock_min / stock_max.

    Se llama después de entradas/salidas.
    """
    negocio = db.query(Negocio).filter(Negocio.id == user["negocio_id"]).first()
    if not negocio:
        return

    negocio_nombre = negocio.nombre_fantasia

    # Producto del negocio
    producto = (
        db.query(Producto)
        .filter(
            Producto.negocio == negocio_nombre,
            func.lower(Producto.nombre) == producto_nombre.lower(),
        )
        .first()
    )
    if not producto:
        return

    # Si no tiene reglas, no genera alertas
    if producto.stock_min is None and producto.stock_max is None:
        return

    # Calcular stock total del producto en el negocio (entradas - salidas)
    movimientos = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == negocio_nombre,
            func.lower(Movimiento.producto) == producto_nombre.lower(),
        )
        .all()
    )

    stock_total = 0
    for m in movimientos:
        qty = m.cantidad or 0
        if m.tipo == "salida" or (m.tipo == "ajuste" and qty < 0):
            stock_total -= abs(qty)
        else:
            stock_total += abs(qty)

    destino = negocio.whatsapp_notificaciones  # puede ser None por ahora

    # 🔴 Alerta por stock mínimo
    if producto.stock_min is not None and stock_total < producto.stock_min:
        mensaje = (
            f"Stock CRÍTICO de '{producto.nombre}': {stock_total} unidades "
            f"(mínimo configurado: {producto.stock_min})."
        )
        crear_alerta_interna(
            db=db,
            negocio_id=negocio.id,
            tipo="stock_min",
            mensaje=mensaje,
            origen=origen,
            destino=destino,
            datos={
                "producto": producto.nombre,
                "stock_total": stock_total,
                "stock_min": producto.stock_min,
            },
        )

    # 🟠 Alerta por sobre-stock
    if producto.stock_max is not None and stock_total > producto.stock_max:
        mensaje = (
            f"SOBRE-STOCK de '{producto.nombre}': {stock_total} unidades "
            f"(máximo configurado: {producto.stock_max})."
        )
        crear_alerta_interna(
            db=db,
            negocio_id=negocio.id,
            tipo="stock_max",
            mensaje=mensaje,
            origen=origen,
            destino=destino,
            datos={
                "producto": producto.nombre,
                "stock_total": stock_total,
                "stock_max": producto.stock_max,
            },
        )


def evaluar_alertas_vencimiento(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
):
    """
    Genera alertas internas cuando un producto está vencido o próximo a vencer.
    Se basa en las fechas de vencimiento registradas en las ENTRADAS.
    """
    negocio = db.query(Negocio).filter(Negocio.id == user["negocio_id"]).first()
    if not negocio:
        return

    negocio_nombre = negocio.nombre_fantasia

    # Filtrar entradas del producto con fecha de vencimiento válida
    entradas = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == negocio_nombre,
            Movimiento.tipo == "entrada",
            func.lower(Movimiento.producto) == producto_nombre.lower(),
            Movimiento.fecha_vencimiento.isnot(None),
        )
        .all()
    )

    if not entradas:
        return

    hoy = date.today()

    for e in entradas:
        fv = e.fecha_vencimiento
        if not fv:
            continue

        dias = (fv - hoy).days  # días restantes
        destino = negocio.whatsapp_notificaciones

        # 🔴 Producto ya vencido
        if dias < 0:
            mensaje = (
                f"ALERTA: El producto '{e.producto}' está VENCIDO "
                f"(fecha: {fv.strftime('%d-%m-%Y')})."
            )
            crear_alerta_interna(
                db=db,
                negocio_id=negocio.id,
                tipo="vencido",
                mensaje=mensaje,
                origen=origen,
                destino=destino,
                datos={
                    "producto": e.producto,
                    "fecha_vencimiento": fv.isoformat(),
                    "dias_restantes": dias,
                },
            )
            continue

        # 🟠 Próximo a vencer (dentro de 7 días)
        if dias <= 7:
            mensaje = (
                f"Advertencia: El producto '{e.producto}' vencerá en {dias} días "
                f"(fecha: {fv.strftime('%d-%m-%Y')})."
            )
            crear_alerta_interna(
                db=db,
                negocio_id=negocio.id,
                tipo="proximo_vencer",
                mensaje=mensaje,
                origen=origen,
                destino=destino,
                datos={
                    "producto": e.producto,
                    "fecha_vencimiento": fv.isoformat(),
                    "dias_restantes": dias,
                },
            )



# ============================
# SUPERADMIN
# ============================

@app.get("/superadmin/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_superadmin(user)

    # Totales de negocios
    total_negocios = db.query(Negocio).count()
    negocios_activos = db.query(Negocio).filter(Negocio.estado == "activo").count()
    negocios_suspendidos = db.query(Negocio).filter(Negocio.estado == "suspendido").count()

    # Alertas pendientes (todas)
    alertas_pendientes = db.query(Alerta).filter(Alerta.estado == "pendiente").count()

    return templates.TemplateResponse(
        "superadmin_dashboard.html",
        {
            "request": request,
            "user": user,
            "total_negocios": total_negocios,
            "negocios_activos": negocios_activos,
            "negocios_suspendidos": negocios_suspendidos,
            "alertas_pendientes": alertas_pendientes,
        },
    )


@app.get("/superadmin/negocios", response_class=HTMLResponse)
async def superadmin_negocios(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_superadmin(user)

    negocios = db.query(Negocio).all()
    data = []

    for n in negocios:
        usuarios = db.query(Usuario).filter(Usuario.negocio_id == n.id).count()
        productos = db.query(Producto).filter(Producto.negocio == n.nombre_fantasia).count()

        hace_30 = datetime.utcnow() - timedelta(days=30)
        movimientos = (
            db.query(Movimiento)
            .filter(Movimiento.negocio == n.nombre_fantasia)
            .filter(Movimiento.fecha >= hace_30)
            .count()
        )

        data.append(
            {
                "id": n.id,
                "nombre": n.nombre_fantasia,
                "plan": n.plan_tipo,
                "estado": n.estado,
                "usuarios": usuarios,
                "productos": productos,
                "movimientos_30d": movimientos,
                "ultimo_acceso": n.ultimo_acceso,
            }
        )

    return templates.TemplateResponse(
        "superadmin_negocios.html",
        {
            "request": request,
            "user": user,
            "negocios": data,
        },
    )


@app.get("/superadmin/negocios/{negocio_id}", response_class=HTMLResponse)
async def superadmin_negocio_detalle(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_superadmin(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    return templates.TemplateResponse(
        "superadmin_negocio_detalle.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "planes": PLANES_CONFIG.keys(),
        },
    )


@app.post("/superadmin/negocios/{negocio_id}/update")
async def superadmin_negocio_update(
    request: Request,
    negocio_id: int,
    plan_tipo: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_superadmin(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    negocio.plan_tipo = plan_tipo
    negocio.estado = estado
    db.commit()

    return RedirectResponse(
        url=f"/superadmin/negocios/{negocio_id}",
        status_code=302,
    )


@app.get("/superadmin/alertas", response_class=HTMLResponse)
async def superadmin_alertas(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_superadmin(user)

    alertas = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(500)
        .all()
    )

    return templates.TemplateResponse(
        "superadmin_alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )

# ============================
# HOME / LOGIN / LOGOUT
# ============================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if is_superadmin(user):
        return RedirectResponse(url="/superadmin/dashboard", status_code=302)

    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        # Si ya está autenticado, lo mandamos a su panel
        if is_superadmin(user):
            return RedirectResponse(url="/superadmin/dashboard", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "user": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = (email or "").strip().lower()

    usuario = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )

    # Validar existencia y estado
    if not usuario or usuario.activo != 1:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Correo o contraseña incorrectos.", "user": None},
            status_code=401,
        )

    # Validar contraseña con bcrypt
    if not verify_password(password, usuario.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Correo o contraseña incorrectos.", "user": None},
            status_code=401,
        )

    # (Opcional) Validar estado del negocio
    if usuario.negocio and usuario.negocio.estado != "activo":
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "El negocio asociado a este usuario está suspendido.",
                "user": None
            },
            status_code=403,
        )

    # Login OK → crear sesión en BD
    token_sesion = crear_sesion_db(db, usuario)

    # Crear respuesta de redirección según rol
    if usuario.rol == "superadmin":
        redirect_url = "/superadmin/dashboard"
    else:
        redirect_url = "/dashboard"

    response = RedirectResponse(url=redirect_url, status_code=302)
    crear_cookie_sesion(response, usuario, token_sesion)
    return response

@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    cookie = request.cookies.get("session")

    if cookie:
        try:
            data = signer.unsign(cookie).decode("utf-8")
            payload = json.loads(data)
            user_id = payload.get("user_id")
            token_sesion = payload.get("token_sesion")

            if user_id and token_sesion:
                db.query(SesionUsuario).filter(
                    SesionUsuario.usuario_id == user_id,
                    SesionUsuario.token_sesion == token_sesion,
                    SesionUsuario.activo == 1,
                ).update({SesionUsuario.activo: 0})
                db.commit()
        except (BadSignature, json.JSONDecodeError):
            # Si la cookie es inválida, simplemente la borramos
            pass

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session")
    return response


# ============================
#     REGISTRAR NEGOCIO
# ============================

@app.get("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_get(request: Request):
    """
    Muestra el formulario para que un dueño cree su negocio + usuario admin.
    Si ya está autenticado, lo mandamos a su dashboard.
    """
    user = get_current_user(request)
    if user:
        if is_superadmin(user):
            return RedirectResponse(url="/superadmin/dashboard", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        "registrar_negocio.html",
        {
            "request": request,
            "errores": [],
            "nombre_negocio": "",
            "whatsapp": "",
            "email": "",
        },
    )


@app.post("/registrar-negocio", response_class=HTMLResponse)
async def registrar_negocio_post(
    request: Request,
    nombre_negocio: str = Form(...),
    whatsapp: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    errores: list[str] = []

    nombre_negocio = (nombre_negocio or "").strip()
    whatsapp = (whatsapp or "").strip()
    email_norm = (email or "").strip().lower()
    password = password or ""
    password2 = password2 or ""

    # Validaciones básicas
    if len(nombre_negocio) < 3:
        errores.append("El nombre del negocio es muy corto (mínimo 3 caracteres).")

    if " " in email_norm or "@" not in email_norm:
        errores.append("Debes ingresar un correo válido.")

    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")

    if password != password2:
        errores.append("Las contraseñas no coinciden.")

    # Validar unicidad de email
    existing_user = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )
    if existing_user:
        errores.append("Ya existe un usuario registrado con ese correo.")

    # Validar unicidad de nombre de negocio
    existing_neg = (
        db.query(Negocio)
        .filter(func.lower(Negocio.nombre_fantasia) == nombre_negocio.lower())
        .first()
    )
    if existing_neg:
        errores.append("Ya existe un negocio con ese nombre de fantasía.")

    if errores:
        # Volver a mostrar el formulario con mensajes
        return templates.TemplateResponse(
            "registrar_negocio.html",
            {
                "request": request,
                "errores": errores,
                "nombre_negocio": nombre_negocio,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=400,
        )

    # Crear negocio + usuario admin
    try:
        negocio = Negocio(
            nombre_fantasia=nombre_negocio,
            whatsapp_notificaciones=whatsapp or None,
            estado="activo",
            # opcional: si quieres fijar explícitamente el plan
            # plan_tipo="demo",
        )
        db.add(negocio)
        db.flush()  # para obtener negocio.id

        usuario_admin = Usuario(
            negocio_id=negocio.id,
            email=email_norm,
            password_hash=hash_password(password),
            rol="admin",
            activo=1,
            nombre_mostrado=None,
        )
        db.add(usuario_admin)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[REGISTRAR_NEGOCIO] Error al crear negocio/usuario: {e}")
        return templates.TemplateResponse(
            "registrar_negocio.html",
            {
                "request": request,
                "errores": ["Ocurrió un error al crear el negocio. Inténtalo nuevamente."],
                "nombre_negocio": nombre_negocio,
                "whatsapp": whatsapp,
                "email": email_norm,
            },
            status_code=500,
        )

    # Login automático del admin recién creado
    token_sesion = crear_sesion_db(db, usuario_admin)
    response = RedirectResponse(url="/dashboard", status_code=302)
    crear_cookie_sesion(response, usuario_admin, token_sesion)
    return response


# ============================
#     REGISTRAR USUARIOS
# ============================

@app.get("/usuarios", response_class=HTMLResponse)
async def listar_usuarios(request: Request, db: Session = Depends(get_db)):
    """
    Lista usuarios (equipo) del negocio actual.
    - admin: ve usuarios de su negocio
    - superadmin: ve todos los usuarios
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    if user["rol"] == "superadmin":
        usuarios = (
            db.query(Usuario)
            .order_by(Usuario.id)
            .all()
        )
    else:
        usuarios = (
            db.query(Usuario)
            .filter(Usuario.negocio_id == user["negocio_id"])
            .order_by(Usuario.id)
            .all()
        )

    return templates.TemplateResponse(
        "usuarios.html",
        {
            "request": request,
            "user": user,
            "usuarios": usuarios,
        },
    )


@app.get("/usuarios/nuevo", response_class=HTMLResponse)
async def nuevo_usuario_get(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    return templates.TemplateResponse(
        "usuarios_nuevo.html",
        {
            "request": request,
            "user": user,
            "errores": [],
            "nombre": "",
            "email": "",
        },
    )


@app.post("/usuarios/nuevo", response_class=HTMLResponse)
async def nuevo_usuario_post(
    request: Request,
    nombre: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    errores: list[str] = []

    nombre = (nombre or "").strip()
    email_norm = (email or "").strip().lower()
    password = password or ""
    password2 = password2 or ""

    # Validaciones básicas
    if " " in email_norm or "@" not in email_norm:
        errores.append("Debes ingresar un correo válido.")

    if len(password) < 8:
        errores.append("La contraseña debe tener al menos 8 caracteres.")

    if password != password2:
        errores.append("Las contraseñas no coinciden.")

    # Validar que no exista otro usuario con el mismo correo
    existing = (
        db.query(Usuario)
        .filter(Usuario.email == email_norm)
        .first()
    )
    if existing:
        errores.append("Ya existe un usuario registrado con ese correo.")

    if errores:
        return templates.TemplateResponse(
            "usuarios_nuevo.html",
            {
                "request": request,
                "user": user,
                "errores": errores,
                "nombre": nombre,
                "email": email_norm,
            },
            status_code=400,
        )

    # Negocio al que pertenecerá el operador
    negocio_id = user["negocio_id"]
    # Aplicar límite de plan
    check_plan_limit(db, negocio_id, "usuarios")

    try:
        nuevo = Usuario(
            negocio_id=negocio_id,
            email=email_norm,
            password_hash=hash_password(password),
            rol="operador",
            activo=1,
            nombre_mostrado=nombre or None,
        )
        db.add(nuevo)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[USUARIOS_NUEVO] Error al crear operador: {e}")
        errores.append("Ocurrió un error al crear el usuario. Inténtalo nuevamente.")
        return templates.TemplateResponse(
            "usuarios_nuevo.html",
            {
                "request": request,
                "user": user,
                "errores": errores,
                "nombre": nombre,
                "email": email_norm,
            },
            status_code=500,
        )

    # futuro: registrar_auditoria(...)
    return RedirectResponse("/usuarios", status_code=302)



# ============================
#     DASHBOARD
# ============================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # El superadmin no usa este dashboard, va al corporativo
    if is_superadmin(user):
        return RedirectResponse("/superadmin/dashboard", status_code=302)

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

    # ===== FLAGS DE ONBOARDING =====
    # ¿Tiene zonas?
    tiene_zonas = (
        db.query(Zona)
        .filter(Zona.negocio == user["negocio"])
        .count() > 0
    )

    # ¿Tiene slots?
    tiene_slots = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Zona.negocio == user["negocio"])
        .count() > 0
    )

    # ¿Tiene productos?
    tiene_productos = total_skus > 0

    # ¿Tiene algún movimiento registrado?
    tiene_movimientos = len(movimientos_all) > 0

    # ============================
    # 3) Totales por producto y lotes (FEFO simplificado)
    # ============================
    totales_producto: dict[str, int] = {}        # producto -> qty total
    lotes_por_producto: dict[str, list[dict]] = {}  # producto -> [{fv, qty}, ...]

    for mov in movimientos_all:
        prod_name = mov.producto or ""
        prod_key = prod_name.lower()
        if not prod_key:
            continue

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
                    l["fv"] or date(9999, 12, 31),
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
    # 4) Resumen de estados por producto (min/max)
    # ============================
    resumen_stock = {
        "Crítico": 0,
        "OK": 0,
        "Sobre-stock": 0,
        "Sin configuración": 0,
    }
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}

    estado_producto: dict[str, str] = {}  # producto -> estado

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
    # 5) Resumen de vencimientos por producto
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
    # 6) Total unidades en stock (suma de positivos)
    # ============================
    total_unidades = sum(q for q in totales_producto.values() if q > 0)

    # 💰 Valor total de inventario (solo cantidades positivas)
    valor_inventario = 0.0
    for prod_key, qty in totales_producto.items():
        if qty <= 0:
            continue
        p = productos_by_name.get(prod_key)
        if p and p.costo_unitario is not None:
            valor_inventario += qty * p.costo_unitario

    valor_inventario = int(round(valor_inventario))

    # 💸 Pérdidas por merma últimos 30 días (motivo_salida = 'merma')
    desde_30 = hoy - timedelta(days=30)
    salidas_merma = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio == user["negocio"],
            Movimiento.tipo == "salida",
            Movimiento.motivo_salida == "merma",
            Movimiento.fecha >= datetime.combine(desde_30, datetime.min.time()),
        )
        .all()
    )

    perdidas_merma_30d = 0.0
    for m in salidas_merma:
        p = productos_by_name.get((m.producto or "").lower())
        if p and p.costo_unitario is not None:
            perdidas_merma_30d += abs(m.cantidad or 0) * p.costo_unitario

    perdidas_merma_30d = int(round(perdidas_merma_30d))

    # ============================
    # 7) Últimos movimientos (tabla)
    # ============================
    movimientos_recientes = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(5)
        .all()
    )

    # ============================
    # 8) Datos para gráfico últimos 7 días (entradas vs salidas)
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

    serie_por_dia: dict[date, dict[str, int]] = {}

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

    chart_data = {
        "labels": chart_labels,
        "entradas": chart_entradas,
        "salidas": chart_salidas,
    }

    # ============================
    # 9) Alertas pendientes del negocio
    # ============================
    alertas_pendientes = 0
    if user.get("negocio_id"):
        alertas_pendientes = (
            db.query(Alerta)
            .filter(
                Alerta.negocio_id == user["negocio_id"],
                Alerta.estado == "pendiente",
            )
            .count()
        )

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
            "valor_inventario": valor_inventario,
            "perdidas_merma_30d": perdidas_merma_30d,
            "alertas_pendientes": alertas_pendientes,
            # flags onboarding
            "tiene_zonas": tiene_zonas,
            "tiene_slots": tiene_slots,
            "tiene_productos": tiene_productos,
            "tiene_movimientos": tiene_movimientos,
        },
    )


# ============================
#     ZONAS
# ============================

@app.get("/zonas", response_class=HTMLResponse)
async def zonas_list(request: Request, db: Session = Depends(get_db)):
    """
    Lista las zonas del negocio actual.
    Solo admin y superadmin pueden acceder.
    """
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
        },
    )


@app.get("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_form(request: Request):
    """
    Formulario para crear una nueva zona en el negocio actual.
    """
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
            "sigla": "",
        },
    )


@app.post("/zonas/nueva", response_class=HTMLResponse)
async def zona_nueva_submit(
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa la creación de una nueva zona.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()

    # Validación: nombre obligatorio
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

    # Si no se entrega sigla, usamos la primera letra del nombre
    if not sigla:
        sigla = nombre[:1].upper()

    # Validar que no exista ya la misma zona en ese negocio (case-insensitive)
    existe = (
        db.query(Zona)
        .filter(
            Zona.negocio == user["negocio"],
            func.lower(Zona.nombre) == nombre.lower(),
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

    # Aplicar límite de plan
    check_plan_limit(db, user["negocio_id"], "zonas")

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
    db: Session = Depends(get_db),
):
    """
    Lista las ubicaciones de una zona específica del negocio actual.
    Solo admin y superadmin.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"],
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
        },
    )


@app.get("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_form(
    zona_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Formulario para crear una nueva ubicación dentro de una zona.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"],
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
            "sigla": "",
        },
    )


@app.post("/zonas/{zona_id}/ubicaciones/nueva", response_class=HTMLResponse)
async def ubicacion_nueva_submit(
    zona_id: int,
    request: Request,
    nombre: str = Form(...),
    sigla: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa la creación de una nueva ubicación en una zona.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    zona = (
        db.query(Zona)
        .filter(
            Zona.id == zona_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )
    if not zona:
        return RedirectResponse("/zonas", status_code=302)

    nombre = (nombre or "").strip()
    sigla = (sigla or "").strip().upper()

    # Validación: nombre obligatorio
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

    # Si no se entrega sigla, se genera a partir de las iniciales del nombre
    if not sigla:
        partes = nombre.split()
        sigla = "".join(p[0] for p in partes).upper()

    # Validar duplicado dentro de la misma zona (case-insensitive)
    existe = (
        db.query(Ubicacion)
        .filter(
            Ubicacion.zona_id == zona.id,
            func.lower(Ubicacion.nombre) == nombre.lower(),
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

    # Aplicar límite de plan
    check_plan_limit(db, user["negocio_id"], "ubicaciones")

    ubicacion = Ubicacion(
        zona_id=zona.id,
        nombre=nombre,
        sigla=sigla,
    )
    db.add(ubicacion)
    db.commit()
    db.refresh(ubicacion)

    print(">>> NUEVA UBICACION:", ubicacion.id, ubicacion.nombre, ubicacion.sigla)

    return RedirectResponse(
        url=f"/zonas/{zona.id}/ubicaciones",
        status_code=302,
    )


# ============================
#     SLOTS
# ============================

@app.get("/ubicaciones/{ubicacion_id}/slots", response_class=HTMLResponse)
async def slots_list(
    ubicacion_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Lista los slots de una ubicación específica del negocio actual.
    Solo accesible para admin y superadmin.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"],
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
        },
    )


@app.get("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_form(
    ubicacion_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Formulario para crear un nuevo slot en una ubicación.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"],
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
        },
    )


@app.post("/ubicaciones/{ubicacion_id}/slots/nuevo", response_class=HTMLResponse)
async def slot_nuevo_submit(
    ubicacion_id: int,
    request: Request,
    codigo: str = Form(...),
    capacidad: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa la creación de un nuevo slot en una ubicación.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    ubicacion = (
        db.query(Ubicacion)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Ubicacion.id == ubicacion_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )
    if not ubicacion:
        return RedirectResponse("/zonas", status_code=302)

    codigo = (codigo or "").strip().upper()
    capacidad_str = (capacidad or "").strip()

    # Validación: código obligatorio
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

    # Validar duplicado de código dentro de la misma ubicación
    existe = (
        db.query(Slot)
        .filter(
            Slot.ubicacion_id == ubicacion.id,
            func.lower(Slot.codigo) == codigo.lower(),
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

    # Parseo de capacidad (opcional)
    capacidad_int = None
    if capacidad_str.isdigit():
        capacidad_int = int(capacidad_str)

    # Aplicar límite de plan
    check_plan_limit(db, user["negocio_id"], "slots")

    # Construcción de código completo usando siglas reales de zona/ubicación
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
        status_code=302,
    )


def get_slots_negocio(db: Session, negocio: str):
    """
    Devuelve todos los slots del negocio con información de zona y ubicación.
    Ideal para poblar selects en formularios de movimientos.
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
    """
    Lista los productos del negocio actual.
    Solo accesible para admin y superadmin.
    """
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
        },
    )


@app.get("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_form(request: Request):
    """
    Formulario de creación de producto.
    """
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
            "stock_min": "",
            "stock_max": "",
            "costo_unitario": "",
        },
    )


@app.post("/productos/nuevo", response_class=HTMLResponse)
async def producto_nuevo_submit(
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    costo_unitario: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa la creación de un nuevo producto del negocio actual.
    """
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

    costo_str = (costo_unitario or "").strip().replace(",", ".")
    try:
        costo_val = float(costo_str) if costo_str else None
    except ValueError:
        return templates.TemplateResponse(
            "producto_nuevo.html",
            {
                "request": request,
                "user": user,
                "error": "El costo unitario debe ser un número válido.",
                "nombre": nombre,
                "unidad": unidad,
                "stock_min": stock_min_str,
                "stock_max": stock_max_str,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    if not nombre:
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
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Validar que no exista ya el mismo nombre para el mismo negocio
    existe = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            func.lower(Producto.nombre) == nombre.lower(),
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
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Aplicar límite de plan
    check_plan_limit(db, user["negocio_id"], "productos")

    producto = Producto(
        negocio=user["negocio"],
        nombre=nombre,
        unidad=unidad,
        stock_min=stock_min_val,
        stock_max=stock_max_val,
        activo=1,
        costo_unitario=costo_val,
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
            "costo_unitario": producto.costo_unitario,
        },
    )

    print(
        ">>> NUEVO PRODUCTO:",
        producto.nombre,
        "min:",
        producto.stock_min,
        "max:",
        producto.stock_max,
    )

    return RedirectResponse(url="/productos", status_code=302)


@app.get("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_form(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Formulario de edición de producto.
    """
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
        },
    )


@app.post("/productos/{producto_id}/editar", response_class=HTMLResponse)
async def producto_editar_submit(
    producto_id: int,
    request: Request,
    nombre: str = Form(...),
    unidad: str = Form(...),
    stock_min: str = Form(""),
    stock_max: str = Form(""),
    costo_unitario: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa la edición de un producto existente del negocio actual.
    """
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

    costo_str = (costo_unitario or "").strip().replace(",", ".")
    try:
        costo_val = float(costo_str) if costo_str else None
    except ValueError:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": "El costo unitario debe ser un número válido.",
                "producto": producto,
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    if not nombre:
        return templates.TemplateResponse(
            "producto_editar.html",
            {
                "request": request,
                "user": user,
                "error": "El nombre del producto no puede estar vacío.",
                "producto": producto,
                "costo_unitario": costo_str,
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
                "costo_unitario": costo_str,
            },
            status_code=400,
        )

    # Guardar cambios
    producto.nombre = nombre
    producto.unidad = unidad
    producto.stock_min = stock_min_val
    producto.stock_max = stock_max_val
    producto.costo_unitario = costo_val

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
            "costo_unitario": producto.costo_unitario,
        },
    )

    return RedirectResponse(url="/productos", status_code=302)

# ============================
#   PRODUCTOS - ACTIVAR / DESACTIVAR
# ============================

@app.post("/productos/{producto_id}/toggle")
async def producto_toggle(
    producto_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # En principio, sólo admin del negocio debería poder cambiar estado
    require_role(user, ("admin",))

    # Buscar producto que pertenezca al negocio del usuario
    producto = (
        db.query(Producto)
        .filter(
            Producto.id == producto_id,
            Producto.negocio == user["negocio"],
        )
        .first()
    )

    if not producto:
        # Si no lo encuentra, devolvemos 404 (y no un JSON raro)
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    # Toggle del estado
    producto.activo = not bool(producto.activo)

    db.commit()

    # Volvemos al listado de productos
    return RedirectResponse("/productos", status_code=302)



# ============================
#     MOVIMIENTOS
# ============================

@app.get("/movimientos", response_class=HTMLResponse)
async def movimientos_view(request: Request, db: Session = Depends(get_db)):
    """
    Listado de movimientos con filtros básicos:
    - rango de fechas
    - tipo de movimiento
    - producto (contiene)
    - usuario (contiene)
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

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
            # si viene mal, simplemente ignoramos el filtro
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

    # Orden más reciente primero + límite de seguridad
    movimientos = (
        query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(500)
        .all()
    )

        # ==========================
    # KPIs sobre los movimientos
    # ==========================
    total_movimientos = len(movimientos)

    def es_ajuste(m: Movimiento) -> bool:
        """
        Consideramos como 'ajuste' cualquier movimiento cuyo motivo_salida
        sea 'ajuste_inventario' (los generados desde /inventario).
        """
        return (m.motivo_salida or "").strip().lower() == "ajuste_inventario"

    # Ajustes (independiente de si suman o restan stock)
    total_ajustes = sum(1 for m in movimientos if es_ajuste(m))

    # Entradas y salidas 'operativas' (no ajustes)
    total_entradas = sum(
        1 for m in movimientos
        if m.tipo == "entrada" and not es_ajuste(m)
    )
    total_salidas = sum(
        1 for m in movimientos
        if m.tipo == "salida" and not es_ajuste(m)
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
            # KPIs
            "total_movimientos": total_movimientos,
            "total_entradas": total_entradas,
            "total_salidas": total_salidas,
            "total_ajustes": total_ajustes,
        },
    )


# ============================
#     MOVIMIENTO DE SALIDA
# ============================

@app.get("/movimientos/salida", response_class=HTMLResponse)
async def salida_form(request: Request, db: Session = Depends(get_db)):
    """
    Formulario para registrar una salida de mercadería.
    - Solo requiere usuario autenticado (operador/admin/superadmin).
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Productos activos del negocio
    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        # Sin productos → forzar flujo a creación de productos
        return RedirectResponse("/productos/nuevo", status_code=302)

    # Slots disponibles del negocio
    slots = get_slots_negocio(db, user["negocio"])
    if not slots:
        # Sin slots → ir a configuración de zonas/ubicaciones/slots
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
        },
    )


@app.post("/movimientos/salida", response_class=HTMLResponse)
async def salida_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    motivo_salida: str = Form(""),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa el formulario de salida:
    - valida stock disponible en la zona/slot
    - registra el movimiento
    - dispara auditoría y evaluación de alertas
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # 🔧 Normalizar entradas
    producto = (producto or "").strip()

    # Buscar slot con su ubicación y zona, asegurando que pertenezca al negocio
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )
    if not slot:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio == user["negocio"],
                Producto.activo == 1,
            )
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

    # 1) Calcular stock actual de ese producto + zona + negocio (entradas - salidas)
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
        error_msg = (
            f"No puedes registrar una salida de {cantidad} unidad(es) de '{producto}' "
            f"en {zona_str} porque el stock actual es {stock_actual}."
        )
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio == user["negocio"],
                Producto.activo == 1,
            )
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
        fecha=datetime.utcnow(),
        motivo_salida=(motivo_salida or None),
    )

    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    # Auditoría
    registrar_auditoria(
        db,
        user,
        accion="salida_creada",
        detalle={
            "movimiento_id": movimiento.id,
            "producto": producto,
            "cantidad": cantidad,
            "zona": zona_str,
            "motivo_salida": motivo_salida or None,
            "comentario": (comentario or "").strip() or None,
        },
    )

    # Evaluar alertas de stock tras la salida
    evaluar_alertas_stock(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="salida",
    )

    print(">>> NUEVA SALIDA:", movimiento.id, producto, cantidad, "en", zona_str)

    return RedirectResponse(url="/dashboard", status_code=302)

# ============================
#     MOVIMIENTO DE ENTRADA
# ============================

@app.get("/movimientos/entrada", response_class=HTMLResponse)
async def entrada_form(request: Request, db: Session = Depends(get_db)):
    """
    Formulario para registrar una entrada de mercadería.
    - Solo requiere usuario autenticado.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Productos activos del negocio
    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        # Sin productos → forzar flujo a creación de producto
        return RedirectResponse("/productos/nuevo", status_code=302)

    # Slots configurados del negocio
    slots = get_slots_negocio(db, user["negocio"])
    if not slots:
        # Sin slots → ir a configurar el diseño del almacén
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "entrada.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_id": "",
            "fecha_vencimiento": "",
        },
    )


@app.post("/movimientos/entrada", response_class=HTMLResponse)
async def entrada_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    fecha_vencimiento: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Procesa el formulario de entrada:
    - valida slot
    - registra movimiento
    - dispara auditoría y reglas de alertas (stock + vencimiento)
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    producto = (producto or "").strip()

    # Buscar slot con su ubicación y zona, validando que pertenezca al negocio
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )
    if not slot:
        # Slot inválido → volver al formulario con mensaje
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
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
                "fecha_vencimiento": fecha_vencimiento,
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

    # Crear movimiento de entrada
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

    # Auditoría
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

    # Evaluar alertas de stock tras la entrada
    evaluar_alertas_stock(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="entrada",
    )

    # 🔔 Evaluar alertas de vencimiento (FEFO simplificado)
    evaluar_alertas_vencimiento(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="entrada",
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
    """
    Formulario para transferir stock entre slots dentro del mismo negocio.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio == user["negocio"],
            Producto.activo == 1,
        )
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
        },
    )


@app.post("/transferencia", response_class=HTMLResponse)
async def transferencia_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_origen_id: int = Form(...),
    slot_destino_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """
    Procesa la transferencia:
    - valida que origen ≠ destino
    - valida slots pertenecen al negocio
    - verifica stock suficiente en el slot origen
    - registra salida en origen y entrada en destino
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    producto = (producto or "").strip()

    # Origen y destino no pueden ser el mismo
    if slot_origen_id == slot_destino_id:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio == user["negocio"],
                Producto.activo == 1,
            )
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

    # Buscar slots de origen y destino, validando que sean del negocio
    slot_origen = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_origen_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )
    slot_destino = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_destino_id,
            Zona.negocio == user["negocio"],
        )
        .first()
    )

    if not slot_origen or not slot_destino:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio == user["negocio"],
                Producto.activo == 1,
            )
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
            .filter(
                Producto.negocio == user["negocio"],
                Producto.activo == 1,
            )
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
        fecha=datetime.utcnow(),
    )

    # 3) Crear entrada en destino
    mov_entrada = Movimiento(
        negocio=user["negocio"],
        usuario=user["email"],
        tipo="entrada",
        producto=producto,
        cantidad=cantidad,
        zona=zona_destino_str,
        fecha=datetime.utcnow(),
    )

    db.add(mov_salida)
    db.add(mov_entrada)
    db.commit()
    db.refresh(mov_salida)
    db.refresh(mov_entrada)

    # Auditoría de la transferencia (una sola entrada con ambos movimientos)
    registrar_auditoria(
        db,
        user,
        accion="transferencia_creada",
        detalle={
            "producto": producto,
            "cantidad": cantidad,
            "zona_origen": zona_origen_str,
            "zona_destino": zona_destino_str,
            "mov_salida_id": mov_salida.id,
            "mov_entrada_id": mov_entrada.id,
        },
    )

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
    """
    Vista de stock consolidado por producto y slot:
    - Calcula stock por slot y por producto (entradas - salidas / ajustes).
    - Evalúa estado por reglas de stock_min / stock_max.
    - Evalúa estado de vencimiento por FEFO basado en movimientos.
    - Aplica filtros por producto, zona, estado y vencimiento.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

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

    totales_producto: dict[str, int] = {}   # prod_key -> qty total
    stock_por_slot: dict[tuple[str, str], dict] = {}
    lotes_por_slot: dict[tuple[str, str], list] = {}

    for mov, slot, ubic, zona in movimientos:
        prod_name = mov.producto
        if not prod_name:
            continue

        prod_key = prod_name.lower()
        zona_str = mov.zona  # D-RA-C1, etc.

        qty = mov.cantidad or 0
        # salidas/ajustes negativos restan, el resto suma
        if mov.tipo == "salida" or (mov.tipo == "ajuste" and qty < 0):
            signed_delta = -abs(qty)
        else:
            signed_delta = abs(qty)

        # Totales por producto (nivel global)
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

        # aseguramos tener las referencias más recientes
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
            # consumir lotes (FEFO)
            qty_to_remove = -signed_delta
            lotes.sort(
                key=lambda l: (
                    l["fv"] is None,
                    l["fv"] or date(9999, 12, 31),
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
    filas_all: list[dict] = []

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

        slot_codigo = slot.codigo if slot is not None else None

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

        filas_all.append(
            {
                "producto": producto_nombre,
                "unidad": prod.unidad if prod else "unidad",
                "zona_nombre": zona_obj.nombre if zona_obj is not None else "-",
                "ubicacion_nombre": ubic.nombre if ubic is not None else "-",
                "codigo_full": slot.codigo_full if slot is not None else zona_str,
                "slot_codigo": slot_codigo or "-",
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
            }
        )

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

            filas_all.append(
                {
                    "producto": p.nombre,
                    "unidad": p.unidad,
                    "zona_nombre": "-",
                    "ubicacion_nombre": "-",
                    "codigo_full": "-",
                    "slot_codigo": "-",
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
                }
            )

    # ============================
    # 4) Opciones para selects (de todas las filas)
    # ============================
    zonas_list = sorted(
        {
            r["zona_nombre"]
            for r in filas_all
            if r["zona_nombre"] and r["zona_nombre"] != "-"
        }
    )
    estados_list = sorted({r["estado"] for r in filas_all})
    venc_list = sorted({r["vencimiento_estado"] for r in filas_all})

    # ============================
    # 5) Aplicar filtros sobre filas_all
    # ============================
    filas_filtradas: list[dict] = []

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
    resumen_estados = {
        "Crítico": 0,
        "OK": 0,
        "Sobre-stock": 0,
        "Sin configuración": 0,
    }
    estado_producto: dict[str, str] = {}
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
        },
    )


# ============================
#      INVENTARIO / CONTEO
# ============================

def _calcular_resumen_inventario(db: Session, negocio_nombre: str) -> dict[tuple[str, str], dict]:
    """
    Calcula el stock teórico por (producto_norm, zona) a partir de la tabla de movimientos.
    Devuelve un dict:
      (producto_norm, zona_norm) -> {
          "producto_display": str,
          "zona": str,
          "entradas": int,
          "salidas": int,
      }
    """
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == negocio_nombre)
        .all()
    )

    resumen: dict[tuple[str, str], dict] = {}

    for m in movimientos:
        nombre_original = (m.producto or "").strip()
        if not nombre_original:
            continue

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
            resumen[key]["entradas"] += m.cantidad or 0
        elif m.tipo == "salida":
            resumen[key]["salidas"] += m.cantidad or 0

    return resumen


@app.get("/inventario", response_class=HTMLResponse)
async def inventario_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # 1. Calcular stock teórico por (producto_norm, zona)
    resumen = _calcular_resumen_inventario(db, user["negocio"])

    # 2. Construir lista para la tabla
    stock_items: list[dict] = []
    for (_prod_norm, _zona_norm), data in resumen.items():
        stock_actual = (data["entradas"] or 0) - (data["salidas"] or 0)
        stock_items.append(
            {
                "producto": data["producto_display"],
                "zona": data["zona"],
                "stock_actual": stock_actual,
            }
        )

    # Ordenamos por zona y nombre
    stock_items.sort(key=lambda x: (x["zona"], x["producto"]))

    return templates.TemplateResponse(
        "inventario.html",
        {
            "request": request,
            "user": user,
            "stock_items": stock_items,
        },
    )


@app.post("/inventario", response_class=HTMLResponse)
async def inventario_submit(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    form = await request.form()
    try:
        total_items = int(form.get("total_items", 0))
    except ValueError:
        total_items = 0

    # 1. Recalcular stock teórico igual que en el GET
    resumen = _calcular_resumen_inventario(db, user["negocio"])

    # 2. Procesar conteos y generar ajustes
    ajustes_realizados = 0

    for i in range(total_items):
        producto = (form.get(f"producto_{i}") or "").strip()
        zona = (form.get(f"zona_{i}") or "").strip()
        conteo_str = form.get(f"conteo_{i}") or ""

        if not producto:
            continue

        try:
            conteo = int(conteo_str)
        except ValueError:
            conteo = 0

        key_norm = (producto.lower(), zona)
        data = resumen.get(key_norm)

        stock_teorico = 0
        if data is not None:
            stock_teorico = (data["entradas"] or 0) - (data["salidas"] or 0)

        diff = conteo - stock_teorico
        if diff == 0:
            continue  # no hay ajuste

        # Si diff > 0 → faltaba stock en el sistema → registramos una "entrada"
        # Si diff < 0 → sobraba stock en el sistema → registramos una "salida"
        tipo_mov = "entrada" if diff > 0 else "salida"
        cantidad_ajuste = abs(diff)

        # 🔹 Marcamos este movimiento explícitamente como AJUSTE DE INVENTARIO
        movimiento = Movimiento(
            negocio=user["negocio"],
            usuario=user["email"],
            tipo=tipo_mov,
            producto=producto,
            cantidad=cantidad_ajuste,
            zona=zona,
            fecha=datetime.utcnow(),
            motivo_salida="ajuste_inventario",
        )
        db.add(movimiento)
        ajustes_realizados += 1

        print(
            f">>> AJUSTE INVENTARIO: {tipo_mov} {cantidad_ajuste} x '{producto}' en {zona} "
            f"(teórico={stock_teorico}, conteo={conteo})"
        )

        # Opcional pero útil: registramos en auditoría
        registrar_auditoria(
            db,
            user,
            accion="ajuste_inventario",
            detalle={
                "producto": producto,
                "zona": zona,
                "tipo_mov": tipo_mov,
                "cantidad_ajuste": cantidad_ajuste,
                "stock_teorico": stock_teorico,
                "conteo": conteo,
                "motivo": "ajuste_inventario",
            },
        )

        db.add(movimiento)
        ajustes_realizados += 1

        print(
            f">>> AJUSTE INVENTARIO: {tipo_mov} {cantidad_ajuste} x '{producto}' en {zona} "
            f"(teórico={stock_teorico}, conteo={conteo})"
        )

        # Opcional pero útil: registramos en auditoría
        registrar_auditoria(
            db,
            user,
            accion="ajuste_inventario",
            detalle={
                "producto": producto,
                "zona": zona,
                "tipo_mov": tipo_mov,
                "cantidad_ajuste": cantidad_ajuste,
                "stock_teorico": stock_teorico,
                "conteo": conteo,
            },
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
        return RedirectResponse("/login", status_code=302)

    # Traer solo movimientos del negocio del usuario
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio == user["negocio"])
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(
        "historial.html",
        {
            "request": request,
            "user": user,
            "movimientos": movimientos,
        },
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
        {
            "request": request,
            "user": user,
            "registros": registros,
        },
    )

# ============================
#      ALERTAS
# ============================

@app.get("/alertas", response_class=HTMLResponse)
async def alertas_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Solo admin y superadmin pueden ver el centro de alertas
    require_role(user, ("admin", "superadmin"))

    alertas = (
        db.query(Alerta)
        .filter(Alerta.negocio_id == user["negocio_id"])
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        "alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )


@app.post("/alertas/{alerta_id}/marcar-leida")
async def alerta_marcar_leida(
    alerta_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    require_role(user, ("admin", "superadmin"))

    alerta = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .filter(
            Alerta.id == alerta_id,
            Negocio.id == user["negocio_id"],
        )
        .first()
    )

    if not alerta:
        raise HTTPException(status_code=404, detail="Alerta no encontrada.")

    if alerta.estado == "pendiente":
        alerta.estado = "leida"
        # dejamos fecha_envio para CUANDO realmente se envíe por WhatsApp/email
        db.commit()

    return RedirectResponse(url="/alertas", status_code=302)


# ============================
#      MAIN
# ============================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "miniWMS:app",
        host="0.0.0.0",
        port=8000,
        reload=DEBUG,  # reload solo en desarrollo
    )
