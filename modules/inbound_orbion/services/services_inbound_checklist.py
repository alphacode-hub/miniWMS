# modules/inbound_orbion/services/services_inbound_checklist.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models import (
    InboundRecepcion,
    InboundChecklistRecepcion,
    InboundChecklistRespuesta,
    InboundPlantillaChecklist,
    InboundPlantillaChecklistItem,
)

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# =========================================================
# CONTRATOS / CONSTANTES
# =========================================================

CHECKLIST_ESTADO_PENDIENTE = "PENDIENTE"
CHECKLIST_ESTADO_EN_PROGRESO = "EN_PROGRESO"
CHECKLIST_ESTADO_COMPLETADO = "COMPLETADO"

TIPO_BOOL = "BOOL"
TIPO_TEXTO = "TEXTO"
TIPO_NUMERO = "NUMERO"
TIPO_FECHA = "FECHA"
TIPO_OPCION = "OPCION"


@dataclass(frozen=True)
class ChecklistItemVM:
    item_id: int
    codigo: str
    nombre: str
    descripcion: Optional[str]
    tipo: str
    requerido: bool
    opciones: Optional[str]
    orden: int

    # respuesta actual (puede ser None)
    ok: Optional[bool]
    valor: Optional[str]
    nota: Optional[str]
    respondido_por: Optional[str]
    actualizado_en: Optional[datetime]

    activo: bool


@dataclass(frozen=True)
class ChecklistRecepcionVM:
    recepcion_id: int
    checklist_id: int
    estado: str
    plantilla_id: Optional[int]
    plantilla_nombre: Optional[str]
    iniciado_en: Optional[datetime]
    completado_en: Optional[datetime]
    actualizado_en: datetime

    items: List[ChecklistItemVM]
    resumen: Dict[str, Any]


# =========================================================
# SEED DEFAULT (MÍNIMO HACCP/OPERATIVO)
# =========================================================

def _seed_default_checklist_items() -> List[Dict[str, Any]]:
    return [
        dict(codigo="SELLOS_OK", nombre="Sellos / cierre de contenedor en buen estado", tipo=TIPO_BOOL, requerido=True, orden=10),
        dict(codigo="DANOS_CAMION", nombre="Camión/Contenedor sin daños evidentes", tipo=TIPO_BOOL, requerido=True, orden=20),

        dict(codigo="TEMP_INGRESO", nombre="Temperatura al ingreso registrada", tipo=TIPO_NUMERO, requerido=False, orden=30,
             descripcion="Registrar °C si aplica (congelado/refrigerado)."),
        dict(codigo="TEMP_CUMPLE", nombre="Temperatura cumple rango esperado", tipo=TIPO_BOOL, requerido=False, orden=40),

        dict(codigo="DOC_GUIA", nombre="Guía / documento de transporte disponible", tipo=TIPO_BOOL, requerido=True, orden=50),
        dict(codigo="DOC_FACTURA", nombre="Factura / documento comercial disponible", tipo=TIPO_BOOL, requerido=False, orden=60),
        dict(codigo="DOC_CERTIFICADOS", nombre="Certificados requeridos disponibles", tipo=TIPO_BOOL, requerido=False, orden=70),

        dict(codigo="EMBALAJE_OK", nombre="Embalaje en buen estado (sin roturas/humedad)", tipo=TIPO_BOOL, requerido=True, orden=80),
        dict(codigo="ROTULADO_OK", nombre="Rotulado/etiquetas legibles y correctas", tipo=TIPO_BOOL, requerido=False, orden=90),

        dict(codigo="OBS_GENERAL", nombre="Observación general", tipo=TIPO_TEXTO, requerido=False, orden=999,
             descripcion="Comentario general de la recepción."),
    ]


def asegurar_plantilla_checklist_default(db: Session, negocio_id: int) -> InboundPlantillaChecklist:
    tpl = (
        db.query(InboundPlantillaChecklist)
        .filter(
            InboundPlantillaChecklist.negocio_id == negocio_id,
            InboundPlantillaChecklist.proveedor_id.is_(None),
            InboundPlantillaChecklist.activo.is_(True),
        )
        .order_by(InboundPlantillaChecklist.id.desc())
        .first()
    )
    if tpl:
        return tpl

    tpl = InboundPlantillaChecklist(
        negocio_id=negocio_id,
        proveedor_id=None,
        nombre="Checklist Default ORBION",
        activo=True,
        created_at=utcnow(),  # respeta tu modelo actual
    )
    db.add(tpl)
    db.flush()

    for raw in _seed_default_checklist_items():
        db.add(
            InboundPlantillaChecklistItem(
                plantilla_id=tpl.id,
                codigo=raw["codigo"],
                nombre=raw["nombre"],
                descripcion=raw.get("descripcion"),
                tipo=raw.get("tipo", TIPO_BOOL),
                requerido=bool(raw.get("requerido", False)),
                opciones=raw.get("opciones"),
                orden=int(raw.get("orden", 0)),
                activo=True,
            )
        )

    db.flush()
    return tpl


# =========================================================
# SELECCIÓN DE PLANTILLA (CONTRATO)
# =========================================================

def seleccionar_plantilla_para_recepcion(db: Session, negocio_id: int, recepcion: InboundRecepcion) -> InboundPlantillaChecklist:
    tpl: Optional[InboundPlantillaChecklist] = None

    if recepcion.proveedor_id:
        tpl = (
            db.query(InboundPlantillaChecklist)
            .filter(
                InboundPlantillaChecklist.negocio_id == negocio_id,
                InboundPlantillaChecklist.proveedor_id == recepcion.proveedor_id,
                InboundPlantillaChecklist.activo.is_(True),
            )
            .order_by(InboundPlantillaChecklist.id.desc())
            .first()
        )

    if not tpl:
        tpl = (
            db.query(InboundPlantillaChecklist)
            .filter(
                InboundPlantillaChecklist.negocio_id == negocio_id,
                InboundPlantillaChecklist.proveedor_id.is_(None),
                InboundPlantillaChecklist.activo.is_(True),
            )
            .order_by(InboundPlantillaChecklist.id.desc())
            .first()
        )

    if not tpl:
        tpl = asegurar_plantilla_checklist_default(db, negocio_id)

    return tpl


# =========================================================
# CABECERA CHECKLIST POR RECEPCIÓN
# =========================================================

def obtener_o_crear_checklist_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> InboundChecklistRecepcion:
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    chk = (
        db.query(InboundChecklistRecepcion)
        .filter(
            InboundChecklistRecepcion.negocio_id == negocio_id,
            InboundChecklistRecepcion.recepcion_id == recepcion_id,
        )
        .first()
    )
    if chk:
        if chk.plantilla_id is None:
            tpl = seleccionar_plantilla_para_recepcion(db, negocio_id, recepcion)
            chk.plantilla_id = tpl.id
            chk.actualizado_en = utcnow()
            db.flush()
        return chk

    tpl = seleccionar_plantilla_para_recepcion(db, negocio_id, recepcion)

    chk = InboundChecklistRecepcion(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        plantilla_id=tpl.id,
        estado=CHECKLIST_ESTADO_PENDIENTE,
        iniciado_en=None,
        completado_en=None,
        actualizado_en=utcnow(),
    )
    db.add(chk)
    db.flush()
    return chk


# =========================================================
# LECTURA PARA UI (VM)
# =========================================================

def _cargar_items_plantilla(db: Session, plantilla_id: int) -> List[InboundPlantillaChecklistItem]:
    return (
        db.query(InboundPlantillaChecklistItem)
        .filter(
            InboundPlantillaChecklistItem.plantilla_id == plantilla_id,
            InboundPlantillaChecklistItem.activo.is_(True),
        )
        .order_by(InboundPlantillaChecklistItem.orden.asc(), InboundPlantillaChecklistItem.id.asc())
        .all()
    )


def _cargar_respuestas_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> Dict[int, InboundChecklistRespuesta]:
    rows = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
        )
        .all()
    )
    return {r.checklist_item_id: r for r in rows}


def _es_respondido(r: Optional[InboundChecklistRespuesta]) -> bool:
    if not r:
        return False
    if r.ok is not None:
        return True
    if r.valor is not None and str(r.valor).strip() != "":
        return True
    if r.nota is not None and str(r.nota).strip() != "":
        return True
    return False


def _requerido_ok(it: InboundPlantillaChecklistItem, r: Optional[InboundChecklistRespuesta]) -> bool:
    """
    Contrato:
    - Requerido BOOL => debe estar ok==True
    - Requerido NO-BOOL => valor no vacío (o ok True si alguien lo usa así)
    """
    if not it.requerido:
        return True

    if it.tipo == TIPO_BOOL:
        return bool(r and r.ok is True)

    v_ok = bool(r and r.valor is not None and str(r.valor).strip() != "")
    return v_ok or bool(r and r.ok is True)


def obtener_checklist_vm(db: Session, negocio_id: int, recepcion_id: int) -> ChecklistRecepcionVM:
    chk = obtener_o_crear_checklist_recepcion(db, negocio_id, recepcion_id)

    plantilla_nombre: Optional[str] = None
    if chk.plantilla_id:
        plantilla = db.query(InboundPlantillaChecklist).filter(InboundPlantillaChecklist.id == chk.plantilla_id).first()
        plantilla_nombre = plantilla.nombre if plantilla else None

    items: List[InboundPlantillaChecklistItem] = _cargar_items_plantilla(db, chk.plantilla_id) if chk.plantilla_id else []
    resp_map = _cargar_respuestas_recepcion(db, negocio_id, recepcion_id)

    total = len(items)
    respondidos = 0
    requeridos_total = 0
    requeridos_ok = 0

    vm_items: List[ChecklistItemVM] = []

    for it in items:
        if it.requerido:
            requeridos_total += 1

        r = resp_map.get(it.id)
        if _es_respondido(r):
            respondidos += 1

        if it.requerido and _requerido_ok(it, r):
            requeridos_ok += 1

        vm_items.append(
            ChecklistItemVM(
                item_id=it.id,
                codigo=it.codigo,
                nombre=it.nombre,
                descripcion=it.descripcion,
                tipo=it.tipo,
                requerido=bool(it.requerido),
                opciones=it.opciones,
                orden=int(it.orden or 0),
                ok=(r.ok if r else None),
                valor=(r.valor if r else None),
                nota=(r.nota if r else None),
                respondido_por=(r.respondido_por if r else None),
                actualizado_en=(getattr(r, "actualizado_en", None) if r else None),
                activo=bool(it.activo),
            )
        )

    progreso = round((respondidos / total) * 100.0, 1) if total > 0 else 0.0

    resumen = {
        "items_total": total,
        "items_respondidos": respondidos,
        "progreso_pct": progreso,
        "requeridos_total": requeridos_total,
        "requeridos_ok": requeridos_ok,
        "requeridos_pendientes": max(requeridos_total - requeridos_ok, 0),
    }

    return ChecklistRecepcionVM(
        recepcion_id=recepcion_id,
        checklist_id=chk.id,
        estado=chk.estado,
        plantilla_id=chk.plantilla_id,
        plantilla_nombre=plantilla_nombre,
        iniciado_en=chk.iniciado_en,
        completado_en=chk.completado_en,
        actualizado_en=chk.actualizado_en or utcnow(),
        items=vm_items,
        resumen=resumen,
    )


# =========================================================
# GUARDADO / UPSERT RESPUESTA
# =========================================================

def guardar_respuesta_item(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    checklist_item_id: int,
    *,
    ok: Optional[bool],
    valor: Optional[str],
    nota: Optional[str],
    respondido_por: Optional[str],
) -> InboundChecklistRespuesta:
    chk = obtener_o_crear_checklist_recepcion(db, negocio_id, recepcion_id)

    if not chk.plantilla_id:
        raise InboundDomainError("Checklist sin plantilla asignada; no es posible responder ítems.")

    it = (
        db.query(InboundPlantillaChecklistItem)
        .filter(
            InboundPlantillaChecklistItem.id == checklist_item_id,
            InboundPlantillaChecklistItem.plantilla_id == chk.plantilla_id,
            InboundPlantillaChecklistItem.activo.is_(True),
        )
        .first()
    )
    if not it:
        raise InboundDomainError("Ítem de checklist inválido para la plantilla actual de la recepción.")

    resp = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
            InboundChecklistRespuesta.checklist_item_id == checklist_item_id,
        )
        .first()
    )

    now = utcnow()
    v_clean = (valor.strip() if isinstance(valor, str) else valor)
    n_clean = (nota.strip() if isinstance(nota, str) else nota)

    if resp is None:
        resp = InboundChecklistRespuesta(
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            plantilla_id=chk.plantilla_id,
            checklist_item_id=checklist_item_id,
            respondido_por=respondido_por,
            ok=ok,  # ✅ puede ser True/False/None
            valor=v_clean,
            nota=n_clean,
            creado_en=now,
            actualizado_en=now,
        )
        db.add(resp)
    else:
        resp.plantilla_id = chk.plantilla_id
        resp.respondido_por = respondido_por
        resp.ok = ok
        resp.valor = v_clean
        resp.nota = n_clean
        resp.actualizado_en = now

    if chk.estado == CHECKLIST_ESTADO_PENDIENTE:
        chk.estado = CHECKLIST_ESTADO_EN_PROGRESO
        chk.iniciado_en = chk.iniciado_en or now
    chk.actualizado_en = now

    db.flush()
    return resp


# =========================================================
# COMPLETAR / REABRIR
# =========================================================

def completar_checklist_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> InboundChecklistRecepcion:
    chk = obtener_o_crear_checklist_recepcion(db, negocio_id, recepcion_id)

    # idempotente
    if chk.estado == CHECKLIST_ESTADO_COMPLETADO:
        return chk

    vm = obtener_checklist_vm(db, negocio_id, recepcion_id)
    pendientes = int(vm.resumen.get("requeridos_pendientes", 0) or 0)
    if pendientes > 0:
        raise InboundDomainError(f"No es posible completar el checklist: faltan {pendientes} requeridos.")

    now = utcnow()
    chk.estado = CHECKLIST_ESTADO_COMPLETADO
    chk.completado_en = chk.completado_en or now
    chk.iniciado_en = chk.iniciado_en or now
    chk.actualizado_en = now
    db.flush()
    return chk


def reabrir_checklist_recepcion(db: Session, negocio_id: int, recepcion_id: int) -> InboundChecklistRecepcion:
    chk = obtener_o_crear_checklist_recepcion(db, negocio_id, recepcion_id)
    now = utcnow()
    chk.estado = CHECKLIST_ESTADO_EN_PROGRESO
    chk.completado_en = None
    chk.iniciado_en = chk.iniciado_en or now
    chk.actualizado_en = now
    db.flush()
    return chk
