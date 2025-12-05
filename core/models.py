# models.py
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Date,
    ForeignKey,
    Float,
    Text,
)
from sqlalchemy.orm import relationship

from core.database import Base


# ============================
#   CLASES DEL MODELO
# ============================

class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    # 🔁 FK al negocio (antes era string)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    usuario = Column(String, index=True, nullable=False)   # email del usuario
    accion = Column(String, index=True, nullable=False)    # etiqueta corta: 'entrada_creada', etc.
    detalle = Column(String, nullable=True)                # JSON o texto

    negocio = relationship("Negocio", back_populates="auditorias")


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

    # Relaciones principales
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
    zonas = relationship(
        "Zona",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    productos = relationship(
        "Producto",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    movimientos = relationship(
        "Movimiento",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    auditorias = relationship(
        "Auditoria",
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

    # 🔁 Antes: negocio = Column(String, ...)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    nombre = Column(String, index=True, nullable=False)
    sigla = Column(String, index=True, nullable=True)

    negocio = relationship("Negocio", back_populates="zonas")

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

    # 🔁 Antes: negocio = Column(String, ...)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    nombre = Column(String, index=True, nullable=False)
    unidad = Column(String, default="unidad", nullable=False)
    stock_min = Column(Integer, nullable=True)
    stock_max = Column(Integer, nullable=True)
    activo = Column(Integer, default=1, nullable=False)
    costo_unitario = Column(Float, nullable=True)  # 👈 antes estaba como FLOAT

    # 🔢 Códigos de identificación
    sku = Column(String, nullable=True, index=True)    # Código interno / SKU
    ean13 = Column(String, nullable=True, index=True)  # Código de barras / EAN / QR simple

    negocio = relationship("Negocio", back_populates="productos")


class Movimiento(Base):
    __tablename__ = "movimientos"

    id = Column(Integer, primary_key=True, index=True)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    usuario = Column(String, index=True, nullable=False)   # email del usuario
    tipo = Column(String, index=True, nullable=False)      # entrada / salida / ajuste / etc.
    producto = Column(String, index=True, nullable=False)  # nombre del producto
    cantidad = Column(Integer, nullable=False)
    zona = Column(String, nullable=False)                  # código_full del slot
    fecha = Column(DateTime, default=datetime.utcnow, nullable=False)
    fecha_vencimiento = Column(Date, nullable=True)
    motivo_salida = Column(String, nullable=True)

    # 🆕 Código físico usado en el movimiento (si aplica)
    codigo_producto = Column(String, nullable=True, index=True)

    negocio = relationship("Negocio", back_populates="movimientos")



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
