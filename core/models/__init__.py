# core/models/__init__.py
"""
Modelos ORM – ORBION (SaaS enterprise)

✔ Multi-tenant estricto (negocio_id)
✔ Estados con Enum controlado
✔ Timestamps UTC timezone-aware
✔ Relaciones claras y coherentes
✔ Preparado para crecimiento + Alembic
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone, date

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Date,
    ForeignKey,
    Float,
    Text,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import Enum as SAEnum

from core.database import Base


# =========================================================
# TIME HELPERS
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# =========================================================
# CORE
# =========================================================

class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)

    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)
    usuario = Column(String, nullable=False, index=True)
    accion = Column(String, nullable=False, index=True)
    detalle = Column(Text, nullable=True)

    negocio = relationship("Negocio", back_populates="auditorias")


class Negocio(Base):
    __tablename__ = "negocios"

    id = Column(Integer, primary_key=True)
    nombre_fantasia = Column(String, unique=True, nullable=False, index=True)
    whatsapp_notificaciones = Column(String, nullable=True)
    estado = Column(String, default="activo", nullable=False)

    plan_tipo = Column(String, default="demo", nullable=False)
    plan_fecha_inicio = Column(Date, nullable=True)
    plan_fecha_fin = Column(Date, nullable=True)
    plan_renovacion_cada_meses = Column(Integer, default=1, nullable=False)
    ultimo_acceso = Column(DateTime(timezone=True), nullable=True)

    usuarios = relationship("Usuario", back_populates="negocio", cascade="all, delete-orphan")
    productos = relationship("Producto", back_populates="negocio", cascade="all, delete-orphan")
    zonas = relationship("Zona", back_populates="negocio", cascade="all, delete-orphan")
    movimientos = relationship("Movimiento", back_populates="negocio", cascade="all, delete-orphan")
    alertas = relationship("Alerta", back_populates="negocio", cascade="all, delete-orphan")
    auditorias = relationship("Auditoria", back_populates="negocio", cascade="all, delete-orphan")

    # --- INBOUND (strings resuelven cuando importamos modelos inbound al final)
    inbound_config = relationship("InboundConfig", back_populates="negocio", uselist=False)
    inbound_recepciones = relationship("InboundRecepcion", back_populates="negocio", cascade="all, delete-orphan")
    inbound_citas = relationship("InboundCita", back_populates="negocio", cascade="all, delete-orphan")
    proveedores = relationship("Proveedor", back_populates="negocio", cascade="all, delete-orphan")
    plantillas_proveedor = relationship("InboundPlantillaProveedor", back_populates="negocio", cascade="all, delete-orphan")
    prealertas_inbound = relationship("InboundPrealerta", back_populates="negocio", cascade="all, delete-orphan")
    checklist_items_inbound = relationship("InboundChecklistItem", back_populates="negocio", cascade="all, delete-orphan")


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint(
            "(rol = 'superadmin' AND negocio_id IS NULL) OR (rol <> 'superadmin' AND negocio_id IS NOT NULL)",
            name="ck_usuario_negocio_por_rol",
        ),
    )

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=True, index=True)

    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    rol = Column(String, default="operador", nullable=False)
    activo = Column(Integer, default=1, nullable=False)
    nombre_mostrado = Column(String, nullable=True)

    negocio = relationship("Negocio", back_populates="usuarios")
    sesiones = relationship("SesionUsuario", back_populates="usuario", cascade="all, delete-orphan")


class SesionUsuario(Base):
    __tablename__ = "sesiones_usuario"

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    token_sesion = Column(String, unique=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    activo = Column(Integer, default=1, nullable=False)

    usuario = relationship("Usuario", back_populates="sesiones")


# =========================================================
# WMS
# =========================================================

class Zona(Base):
    __tablename__ = "zonas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    nombre = Column(String, nullable=False, index=True)
    sigla = Column(String, nullable=True)

    negocio = relationship("Negocio", back_populates="zonas")
    ubicaciones = relationship("Ubicacion", back_populates="zona", cascade="all, delete-orphan")


class Ubicacion(Base):
    __tablename__ = "ubicaciones"

    id = Column(Integer, primary_key=True)
    zona_id = Column(Integer, ForeignKey("zonas.id"), nullable=False, index=True)
    nombre = Column(String, nullable=False, index=True)
    sigla = Column(String, nullable=True)

    zona = relationship("Zona", back_populates="ubicaciones")
    slots = relationship("Slot", back_populates="ubicacion", cascade="all, delete-orphan")


class Slot(Base):
    __tablename__ = "slots"

    id = Column(Integer, primary_key=True)
    ubicacion_id = Column(Integer, ForeignKey("ubicaciones.id"), nullable=False, index=True)
    codigo = Column(String, nullable=False, index=True)
    capacidad = Column(Integer, nullable=True)
    codigo_full = Column(String, nullable=False, index=True)

    ubicacion = relationship("Ubicacion", back_populates="slots")


class Producto(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    nombre = Column(String, nullable=False, index=True)
    unidad = Column(String, default="unidad", nullable=False)
    stock_min = Column(Integer, nullable=True)
    stock_max = Column(Integer, nullable=True)
    activo = Column(Integer, default=1, nullable=False)
    costo_unitario = Column(Float, nullable=True)

    sku = Column(String, nullable=True, index=True)
    ean13 = Column(String, nullable=True, index=True)
    origen = Column(String, default="core")

    negocio = relationship("Negocio", back_populates="productos")
    plantillas_proveedor_lineas = relationship(
        "InboundPlantillaProveedorLinea",
        back_populates="producto",
        cascade="all, delete-orphan",
    )


class Movimiento(Base):
    __tablename__ = "movimientos"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    usuario = Column(String, nullable=False, index=True)
    tipo = Column(String, nullable=False, index=True)
    producto = Column(String, nullable=False, index=True)

    cantidad = Column(Float, nullable=False)
    zona = Column(String, nullable=False)
    fecha = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    fecha_vencimiento = Column(Date, nullable=True)
    motivo_salida = Column(String, nullable=True)
    codigo_producto = Column(String, nullable=True, index=True)

    negocio = relationship("Negocio", back_populates="movimientos")


class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    tipo = Column(String, nullable=False)
    mensaje = Column(String, nullable=False)
    destino = Column(String, nullable=True)

    estado = Column(String, default="pendiente", nullable=False)
    fecha_creacion = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    fecha_envio = Column(DateTime(timezone=True), nullable=True)

    origen = Column(String, nullable=True)
    datos_json = Column(Text, nullable=True)

    negocio = relationship("Negocio", back_populates="alertas")


# =========================================================
# INBOUND – IMPORTS (REGISTRAN MAPPERS)
# =========================================================
from core.models.inbound import (  # noqa: E402
    InboundConfig,
    Proveedor,
    InboundPlantillaProveedor,
    InboundPlantillaProveedorLinea,
    InboundCita,
    InboundRecepcion,
    InboundLinea,
    InboundPallet,
    InboundPalletItem,
    InboundPrealerta,
    InboundChecklistItem,
    InboundChecklistRespuesta,
    InboundIncidencia,
    InboundFoto,
    InboundDocumento,
)

from core.models.enums import (  # noqa: E402
    RecepcionEstado,
    PalletEstado,
    IncidenciaEstado,
    CitaEstado,
)

from core.models.time import utcnow