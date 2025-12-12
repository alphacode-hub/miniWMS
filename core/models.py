# core/models.py
from datetime import datetime, date

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
#   CLASES DEL MODELO BASE
# ============================

class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

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
    inbound_config = relationship(
        "InboundConfig",
        back_populates="negocio",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 🆕 Relaciones inbound premium
    inbound_recepciones = relationship(
        "InboundRecepcion",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    inbound_citas = relationship(
        "InboundCita",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    checklist_items_inbound = relationship(
        "InboundChecklistItem",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    proveedores = relationship(
        "Proveedor",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    plantillas_proveedor = relationship(
        "InboundPlantillaProveedor",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )
    prealertas_inbound = relationship(
        "InboundPrealerta",
        back_populates="negocio",
        cascade="all, delete-orphan",
    )


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    # superadmin / admin / operador / operador_inbound / supervisor_inbound / auditor_inbound / transportista
    rol = Column(String, nullable=False, default="operador")
    activo = Column(Integer, nullable=False, default=1)       # 1 = activo, 0 = inactivo
    nombre_mostrado = Column(String, nullable=True)

    negocio = relationship("Negocio", back_populates="usuarios")
    sesiones = relationship(
        "SesionUsuario",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )

    # Relación como creador / responsable en varias entidades inbound
    inbound_citas_creadas = relationship(
        "InboundCita",
        back_populates="creado_por",
        foreign_keys="InboundCita.creado_por_id",
    )
    inbound_incidencias_creadas = relationship(
        "InboundIncidencia",
        back_populates="creado_por",
        foreign_keys="InboundIncidencia.creado_por_id",
    )
    inbound_incidencias_responsable = relationship(
        "InboundIncidencia",
        back_populates="responsable",
        foreign_keys="InboundIncidencia.responsable_id",
    )
    inbound_firmas_realizadas = relationship(
        "InboundFirma",
        back_populates="firmado_por_usuario",
        foreign_keys="InboundFirma.firmado_por_usuario_id",
    )
    inbound_checklist_respuestas = relationship(
        "InboundChecklistRespuesta",
        back_populates="respondido_por",
        foreign_keys="InboundChecklistRespuesta.respondido_por_id",
    )


class SesionUsuario(Base):
    """
    Control de sesiones activas por usuario.
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
    negocio_id = Column(Integer, ForeignKey("negocios.id"), index=True, nullable=False)

    nombre = Column(String, index=True, nullable=False)
    unidad = Column(String, default="unidad", nullable=False)
    stock_min = Column(Integer, nullable=True)
    stock_max = Column(Integer, nullable=True)
    activo = Column(Integer, default=1, nullable=False)
    costo_unitario = Column(Float, nullable=True)

    sku = Column(String, nullable=True, index=True)
    ean13 = Column(String, nullable=True, index=True)

    origen = Column(String, default="core")

    negocio = relationship("Negocio", back_populates="productos")

    # Relación con plantillas de proveedor
    plantillas_proveedor_lineas = relationship(
        "InboundPlantillaProveedorLinea",
        back_populates="producto",
        cascade="all, delete-orphan",
    )


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

    codigo_producto = Column(String, nullable=True, index=True)

    negocio = relationship("Negocio", back_populates="movimientos")


class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    tipo = Column(String, nullable=False)      # stock_min, stock_max, vencimiento, etc.
    mensaje = Column(String, nullable=False)
    destino = Column(String, nullable=True)    # whatsapp, email

    estado = Column(String, nullable=False, default="pendiente")  # pendiente, leida, enviada, error
    fecha_creacion = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    fecha_envio = Column(DateTime, nullable=True)

    origen = Column(String, nullable=True)     # 'entrada', 'salida', 'sistema', etc.
    datos_json = Column(String, nullable=True) # JSON opcional

    negocio = relationship("Negocio", back_populates="alertas")


# ============================
#   INBOUND: RECEPCIONES
# ============================

class InboundRecepcion(Base):
    __tablename__ = "inbound_recepciones"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False)

    codigo = Column(String(50), unique=True, nullable=False, index=True)

    # Proveedor (string para compatibilidad rápida)
    proveedor = Column(String(150), nullable=False)
    referencia_externa = Column(String(150), nullable=True)
    contenedor = Column(String(50), nullable=True)
    patente_camion = Column(String(20), nullable=True)
    tipo_carga = Column(String(50), nullable=True)

    estado = Column(String(30), nullable=False, default="PRE_REGISTRADO")

    fecha_estimada_llegada = Column(DateTime, nullable=True)
    fecha_arribo = Column(DateTime, nullable=True)
    fecha_inicio_descarga = Column(DateTime, nullable=True)
    fecha_fin_descarga = Column(DateTime, nullable=True)

    observaciones = Column(Text, nullable=True)

    creado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relación con cita (dock scheduling)
    cita_id = Column(Integer, ForeignKey("inbound_citas.id"), nullable=True, index=True)

    # Checklist y firma
    checklist_completado = Column(Boolean, default=False, nullable=False)
    checklist_completado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    checklist_completado_en = Column(DateTime, nullable=True)

    # Estado de documentación
    documentacion_completa = Column(Boolean, default=False, nullable=False)

    # Pesaje global de la recepción (camión / contenedor)
    peso_bruto_kg = Column(Float, nullable=True)
    peso_tara_kg = Column(Float, nullable=True)
    peso_neto_kg = Column(Float, nullable=True)
    peso_diferencia_kg = Column(Float, nullable=True)
    origen_peso = Column(String(30), nullable=True)  # 'balanza', 'documento', etc.

    negocio = relationship("Negocio", back_populates="inbound_recepciones")
    creado_por = relationship("Usuario", foreign_keys=[creado_por_id])

    cita = relationship("InboundCita", back_populates="recepcion", uselist=False)
    checklist_completado_por = relationship(
        "Usuario",
        foreign_keys=[checklist_completado_por_id],
    )

    lineas = relationship(
        "InboundLinea",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    incidencias = relationship(
        "InboundIncidencia",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    documentos = relationship(
        "InboundDocumento",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    fotos = relationship(
        "InboundFoto",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    checklist_respuestas = relationship(
        "InboundChecklistRespuesta",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    firmas = relationship(
        "InboundFirma",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )
    pallets = relationship(
        "InboundPallet",
        back_populates="recepcion",
        cascade="all, delete-orphan",
    )


class InboundLinea(Base):
    __tablename__ = "inbound_lineas"

    id = Column(Integer, primary_key=True, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False)

    lote = Column(String(100), nullable=True)
    fecha_vencimiento = Column(DateTime, nullable=True)

    cantidad_esperada = Column(Float, nullable=True)
    cantidad_recibida = Column(Float, nullable=True)
    unidad = Column(String(30), nullable=True)

    temperatura_objetivo = Column(Float, nullable=True)
    temperatura_recibida = Column(Float, nullable=True)
    # Flag rápido para saber si la línea tuvo problema de temperatura
    temperatura_fuera_rango = Column(Boolean, default=False, nullable=False)

    observaciones = Column(Text, nullable=True)

    peso_kg = Column(Float, nullable=True)
    bultos = Column(Integer, nullable=True)

    recepcion = relationship("InboundRecepcion", back_populates="lineas")
    producto = relationship("Producto")

    fotos = relationship(
        "InboundFoto",
        back_populates="linea",
        cascade="all, delete-orphan",
    )
    pallet_items = relationship(
        "InboundPalletItem",
        back_populates="linea",
        cascade="all, delete-orphan",
    )


class InboundIncidencia(Base):
    __tablename__ = "inbound_incidencias"

    id = Column(Integer, primary_key=True, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False)

    tipo = Column(String(50), nullable=False)      # daño, faltante, sobrante, etc.
    criticidad = Column(String(20), nullable=False, default="media")  # baja, media, alta
    descripcion = Column(Text, nullable=False)

    creado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Workflow avanzado
    estado = Column(String(20), nullable=False, default="CREADA")  # CREADA / EN_ANALISIS / CERRADA
    responsable_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    cerrado_en = Column(DateTime, nullable=True)
    accion_correctiva = Column(Text, nullable=True)
    datos_json = Column(Text, nullable=True)  # info extra de análisis

    recepcion = relationship("InboundRecepcion", back_populates="incidencias")
    creado_por = relationship("Usuario", foreign_keys=[creado_por_id])
    responsable = relationship("Usuario", foreign_keys=[responsable_id])

    fotos = relationship(
        "InboundFoto",
        back_populates="incidencia",
        cascade="all, delete-orphan",
    )


class InboundConfig(Base):
    __tablename__ = "inbound_config"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, unique=True, index=True)

    sla_espera_obj_min = Column(Float, nullable=True)
    sla_descarga_obj_min = Column(Float, nullable=True)
    sla_total_obj_min = Column(Float, nullable=True)

    max_incidencias_criticas_por_recepcion = Column(Integer, nullable=True)

    habilitar_alertas_sla = Column(Boolean, default=True, nullable=False)
    habilitar_alertas_incidencias = Column(Boolean, default=True, nullable=False)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    negocio = relationship("Negocio", back_populates="inbound_config")


# ============================
#   INBOUND: CITAS / DOCK SCHEDULING
# ============================

class InboundCita(Base):
    __tablename__ = "inbound_citas"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    proveedor = Column(String(150), nullable=True)
    transportista = Column(String(150), nullable=True)
    patente_camion = Column(String(20), nullable=True)
    nombre_conductor = Column(String(150), nullable=True)

    fecha_hora_cita = Column(DateTime, nullable=False, index=True)
    fecha_hora_llegada_real = Column(DateTime, nullable=True)

    estado = Column(String(30), nullable=False, default="PROGRAMADA")
    # PROGRAMADA / ARRIBADO / RETRASADO / CANCELADA / COMPLETADA

    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=True)

    observaciones = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    creado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)

    negocio = relationship("Negocio", back_populates="inbound_citas")
    recepcion = relationship("InboundRecepcion", back_populates="cita")
    creado_por = relationship("Usuario", back_populates="inbound_citas_creadas")


# ============================
#   INBOUND: DOCUMENTOS
# ============================

class InboundDocumento(Base):
    __tablename__ = "inbound_documentos"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)

    tipo = Column(String(50), nullable=False)  # GUIA, FACTURA, PACKING_LIST, CERTIFICADO, OTRO
    nombre_archivo = Column(String(255), nullable=False)
    ruta_archivo = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=True)

    es_obligatorio = Column(Boolean, default=False, nullable=False)
    es_validado = Column(Boolean, default=False, nullable=False)

    subido_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    subido_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    observaciones = Column(Text, nullable=True)

    negocio = relationship("Negocio")
    recepcion = relationship("InboundRecepcion", back_populates="documentos")
    subido_por = relationship("Usuario")


# ============================
#   INBOUND: FOTOS / EVIDENCIAS
# ============================

class InboundFoto(Base):
    __tablename__ = "inbound_fotos"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)

    # Opcionalmente asociada a línea e incidencia
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), nullable=True, index=True)
    incidencia_id = Column(Integer, ForeignKey("inbound_incidencias.id"), nullable=True, index=True)

    tipo = Column(String(50), nullable=True)  # CONTENEDOR, DAÑO, PALLET, DOCUMENTO, OTRO
    descripcion = Column(Text, nullable=True)

    ruta_archivo = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=True)

    subido_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    subido_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    negocio = relationship("Negocio")
    recepcion = relationship("InboundRecepcion", back_populates="fotos")
    linea = relationship("InboundLinea", back_populates="fotos")
    incidencia = relationship("InboundIncidencia", back_populates="fotos")
    subido_por = relationship("Usuario")


# ============================
#   INBOUND: CHECKLIST CONFIGURABLE (AVANZADO)
# ============================

class InboundChecklistItem(Base):
    __tablename__ = "inbound_checklist_items"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    texto = Column(String(255), nullable=False)
    orden = Column(Integer, nullable=False, default=1)
    activo = Column(Boolean, default=True, nullable=False)

    # Campos de configuración avanzada
    es_obligatorio = Column(Boolean, default=False, nullable=False)
    requiere_valor_bool = Column(Boolean, default=True, nullable=False)       # Sí/No
    permite_comentario = Column(Boolean, default=True, nullable=False)       # Texto libre
    permite_valor_numerico = Column(Boolean, default=False, nullable=False)  # Ej: temperatura medida
    requiere_foto = Column(Boolean, default=False, nullable=False)           # Evidencia visual requerida

    negocio = relationship("Negocio", back_populates="checklist_items_inbound")

    respuestas = relationship(
        "InboundChecklistRespuesta",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class InboundChecklistRespuesta(Base):
    __tablename__ = "inbound_checklist_respuestas"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inbound_checklist_items.id"), nullable=False, index=True)

    # Valores posibles según configuración del item
    valor_bool = Column(Boolean, nullable=True)        # Sí / No
    valor_texto = Column(Text, nullable=True)          # Comentario
    valor_numerico = Column(Float, nullable=True)      # Ej: temperatura medida, cantidad, etc.
    ruta_foto = Column(String(500), nullable=True)     # Evidencia visual asociada

    respondido_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)
    respondido_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    negocio = relationship("Negocio")
    recepcion = relationship("InboundRecepcion", back_populates="checklist_respuestas")
    item = relationship("InboundChecklistItem", back_populates="respuestas")
    respondido_por = relationship("Usuario", back_populates="inbound_checklist_respuestas")


# ============================
#   INBOUND: FIRMAS DIGITALES
# ============================

class InboundFirma(Base):
    __tablename__ = "inbound_firmas"

    id = Column(Integer, primary_key=True, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)

    tipo = Column(String(30), nullable=False)  # transportista / supervisor
    nombre_firmante = Column(String(150), nullable=True)
    documento_firmante = Column(String(50), nullable=True)  # rut, DNI, etc.

    imagen_path = Column(String(255), nullable=False)  # ruta del PNG de la firma

    firmado_por_usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)
    firmado_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    recepcion = relationship("InboundRecepcion", back_populates="firmas")
    firmado_por_usuario = relationship("Usuario", back_populates="inbound_firmas_realizadas")


# ============================
#   INBOUND: PALLET BUILDER
# ============================

class InboundPallet(Base):
    __tablename__ = "inbound_pallets"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    recepcion_id = Column(Integer, ForeignKey("inbound_recepciones.id"), nullable=False, index=True)

    codigo_pallet = Column(String(50), nullable=False, index=True)  # Ej: PAL-000001
    peso_bruto_kg = Column(Float, nullable=True)
    peso_tara_kg = Column(Float, nullable=True)
    peso_neto_kg = Column(Float, nullable=True)

    bultos = Column(Integer, nullable=True)
    temperatura_promedio = Column(Float, nullable=True)
    observaciones = Column(Text, nullable=True)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    creado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)

    negocio = relationship("Negocio")
    recepcion = relationship("InboundRecepcion", back_populates="pallets")
    creado_por = relationship("Usuario")

    items = relationship(
        "InboundPalletItem",
        back_populates="pallet",
        cascade="all, delete-orphan",
    )


class InboundPalletItem(Base):
    __tablename__ = "inbound_pallet_items"

    id = Column(Integer, primary_key=True, index=True)
    pallet_id = Column(Integer, ForeignKey("inbound_pallets.id"), nullable=False, index=True)
    linea_id = Column(Integer, ForeignKey("inbound_lineas.id"), nullable=False, index=True)

    cantidad = Column(Float, nullable=True)
    peso_kg = Column(Float, nullable=True)

    pallet = relationship("InboundPallet", back_populates="items")
    linea = relationship("InboundLinea", back_populates="pallet_items")


# ============================
#   PROVEEDORES + PLANTILLAS
# ============================

class Proveedor(Base):
    __tablename__ = "proveedores"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)

    nombre = Column(String(150), nullable=False, index=True)
    rut = Column(String(50), nullable=True, index=True)
    contacto = Column(String(150), nullable=True)
    telefono = Column(String(50), nullable=True)
    email = Column(String(150), nullable=True)
    direccion = Column(String(255), nullable=True)

    activo = Column(Boolean, default=True, nullable=False)
    observaciones = Column(Text, nullable=True)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    negocio = relationship("Negocio", back_populates="proveedores")

    plantillas = relationship(
        "InboundPlantillaProveedor",
        back_populates="proveedor",
        cascade="all, delete-orphan",
    )


class InboundPlantillaProveedor(Base):
    __tablename__ = "inbound_plantillas_proveedor"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), nullable=False, index=True)

    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text, nullable=True)
    activo = Column(Boolean, default=True, nullable=False)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)

    negocio = relationship("Negocio", back_populates="plantillas_proveedor")
    proveedor = relationship("Proveedor", back_populates="plantillas")

    lineas = relationship(
        "InboundPlantillaProveedorLinea",
        back_populates="plantilla",
        cascade="all, delete-orphan",
    )


class InboundPlantillaProveedorLinea(Base):
    __tablename__ = "inbound_plantillas_proveedor_lineas"

    id = Column(Integer, primary_key=True, index=True)
    plantilla_id = Column(Integer, ForeignKey("inbound_plantillas_proveedor.id"), nullable=False, index=True)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False, index=True)

    cantidad_sugerida = Column(Float, nullable=True)
    unidad = Column(String(30), nullable=True)
    peso_kg_sugerido = Column(Float, nullable=True)

    plantilla = relationship("InboundPlantillaProveedor", back_populates="lineas")
    producto = relationship("Producto", back_populates="plantillas_proveedor_lineas")


# ============================
#   INBOUND: PREALERTAS
# ============================

class InboundPrealerta(Base):
    __tablename__ = "inbound_prealertas"

    id = Column(Integer, primary_key=True, index=True)
    negocio_id = Column(Integer, ForeignKey("negocios.id"), nullable=False, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), nullable=True, index=True)

    codigo = Column(String(50), nullable=False, index=True)  # código de prealerta
    estado = Column(String(30), nullable=False, default="ABIERTA")  # ABIERTA / VINCULADA / CERRADA

    contenedor = Column(String(50), nullable=True)
    tipo_carga = Column(String(50), nullable=True)
    fecha_estimada_llegada = Column(DateTime, nullable=True)

    # Por simplicidad inicial se puede guardar detalle en JSON, luego se normaliza a líneas
    detalle_json = Column(Text, nullable=True)

    observaciones = Column(Text, nullable=True)

    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    negocio = relationship("Negocio", back_populates="prealertas_inbound")
    proveedor = relationship("Proveedor")
