# modules/inbound_orbion/services/checklist.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.models.time import utcnow
from core.models.enums import InboundChecklistItemEstado
from core.models import (
    InboundRecepcion,
    InboundChecklistPlantilla,
    InboundChecklistSeccion,
    InboundChecklistItem,
    InboundChecklistEjecucion,
    InboundChecklistRespuesta,
)

from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)

# =========================================================
# CONTRATO ORBION CHECKLIST (SIMPLE V2)
# =========================================================

EST_PENDIENTE = InboundChecklistItemEstado.PENDIENTE.value
EST_CUMPLE = InboundChecklistItemEstado.CUMPLE.value
EST_NO_CUMPLE = InboundChecklistItemEstado.NO_CUMPLE.value
EST_NA = InboundChecklistItemEstado.NA.value

EST_SET = {EST_PENDIENTE, EST_CUMPLE, EST_NO_CUMPLE, EST_NA}


# =========================================================
# VIEW MODELS (UI)
# =========================================================

@dataclass(frozen=True)
class ChecklistItemVM:
    item_id: int
    seccion_id: int
    seccion_codigo: str
    seccion_titulo: str
    seccion_orden: int

    codigo: str
    nombre: str
    descripcion: Optional[str]

    requerido: bool
    critico: bool
    orden: int
    activo: bool

    # respuesta actual
    estado: str
    nota: Optional[str]
    respondido_por: Optional[str]
    respondido_en: Optional[datetime]
    actualizado_en: Optional[datetime]


@dataclass(frozen=True)
class ChecklistSeccionVM:
    seccion_id: int
    codigo: str
    titulo: str
    orden: int
    items: List[ChecklistItemVM]
    resumen: Dict[str, Any]


@dataclass(frozen=True)
class ChecklistRecepcionVM:
    recepcion_id: int

    ejecucion_id: int
    plantilla_id: int
    plantilla_nombre: str

    creado_en: datetime
    actualizado_en: datetime

    items: List[ChecklistItemVM]          # lista plana
    secciones: List[ChecklistSeccionVM]   # agrupado para template
    resumen: Dict[str, Any]               # cálculo “resultado sugerido”, progreso, bloqueos


# =========================================================
# HELPERS
# =========================================================

def _s(v: Optional[str]) -> str:
    return (v or "").strip()


def _upper(v: Optional[str]) -> str:
    return _s(v).upper()


def _estado_normalize(valor: Any) -> str:
    """
    Normaliza entradas de UI a contrato SIMPLE V2:
      PENDIENTE | CUMPLE | NO_CUMPLE | NA
    Acepta compat:
      OK/CONFORME -> CUMPLE
      NOK/NO CONFORME -> NO_CUMPLE
      N/A -> NA
    """
    v = _upper(None if valor is None else str(valor))

    if v in ("", "PEND", "PENDIENTE"):
        return EST_PENDIENTE

    if v in ("CUMPLE", "OK", "CONFORME", "SI", "SÍ", "YES", "TRUE"):
        return EST_CUMPLE

    if v in ("NO_CUMPLE", "NOK", "NO CONFORME", "NO_CONFORME", "NO", "FALSE"):
        return EST_NO_CUMPLE

    if v in ("NA", "N/A", "NO_APLICA", "NO APLICA"):
        return EST_NA

    # default seguro
    return EST_PENDIENTE


def _es_respondido(estado: str, nota: Optional[str]) -> bool:
    """
    ENTERPRISE RULE (alineado a UI):
    Un ítem cuenta como 'respondido' SOLO si estado != PENDIENTE.
    La nota NO suma progreso.
    """
    st = _upper(estado)
    return st in (EST_CUMPLE, EST_NO_CUMPLE, EST_NA)


def _build_secciones(vm_items: List[ChecklistItemVM]) -> List[ChecklistSeccionVM]:
    key_to_items: Dict[Tuple[int, str, str, int], List[ChecklistItemVM]] = {}

    for it in vm_items:
        key = (it.seccion_id, it.seccion_codigo, it.seccion_titulo, int(it.seccion_orden))
        key_to_items.setdefault(key, []).append(it)

    secciones: List[ChecklistSeccionVM] = []
    for (sec_id, codigo, titulo, orden) in sorted(key_to_items.keys(), key=lambda k: (k[3], k[1], k[0])):
        arr = key_to_items[(sec_id, codigo, titulo, orden)]
        arr.sort(key=lambda x: (x.orden, x.item_id))

        total = len(arr)
        respondidos = 0
        no_cumple = 0
        criticos_no_cumple = 0
        requeridos_total = 0
        requeridos_ok = 0

        for it in arr:
            if it.requerido:
                requeridos_total += 1

            if _es_respondido(it.estado, it.nota):
                respondidos += 1

            if it.estado == EST_NO_CUMPLE:
                no_cumple += 1
                if it.critico:
                    criticos_no_cumple += 1

            if it.requerido and it.estado == EST_CUMPLE:
                requeridos_ok += 1

        pct = round((respondidos / total) * 100.0, 1) if total > 0 else 0.0
        resumen = {
            "items_total": total,
            "items_respondidos": respondidos,
            "progreso_pct": pct,
            "no_cumple": no_cumple,
            "criticos_no_cumple": criticos_no_cumple,
            "requeridos_total": requeridos_total,
            "requeridos_ok": requeridos_ok,
            "requeridos_pendientes": max(requeridos_total - requeridos_ok, 0),
        }

        secciones.append(
            ChecklistSeccionVM(
                seccion_id=sec_id,
                codigo=codigo,
                titulo=titulo,
                orden=orden,
                items=arr,
                resumen=resumen,
            )
        )

    return secciones


# =========================================================
# SEED DEFAULT (SIMPLE V2)
# =========================================================

def _seed_default_simple_v2() -> List[Dict[str, Any]]:
    """
    Define plantilla simple por secciones + items.
    Sin parent/children ni tipos (modelo simple).
    """
    return [
        # -------------------------
        # DOCS
        # -------------------------
        dict(sec_codigo="DOCS", sec_titulo="Documentos", sec_orden=10,
             item_codigo="DOCS_ESTADO", item_nombre="Documentación asociada a la recepción conforme",
             item_desc=None, requerido=True, critico=True, orden=10),
        dict(sec_codigo="DOCS", sec_titulo="Documentos", sec_orden=10,
             item_codigo="DOCS_GUIA", item_nombre="Guía de despacho registrada",
             item_desc=None, requerido=False, critico=False, orden=20),
        dict(sec_codigo="DOCS", sec_titulo="Documentos", sec_orden=10,
             item_codigo="DOCS_FACTURA", item_nombre="Factura registrada",
             item_desc=None, requerido=False, critico=False, orden=30),
        dict(sec_codigo="DOCS", sec_titulo="Documentos", sec_orden=10,
             item_codigo="DOCS_CERT", item_nombre="Certificados registrados",
             item_desc=None, requerido=False, critico=False, orden=40),

        # -------------------------
        # COND
        # -------------------------
        dict(sec_codigo="COND", sec_titulo="Condición", sec_orden=20,
             item_codigo="COND_TRANSPORTE", item_nombre="Condición del transporte conforme",
             item_desc=None, requerido=True, critico=True, orden=10),
        dict(sec_codigo="COND", sec_titulo="Condición", sec_orden=20,
             item_codigo="COND_MERCADERIA", item_nombre="Condición general de la mercadería conforme",
             item_desc="Incluye embalaje y rotulado en una sola evaluación.",
             requerido=True, critico=True, orden=20),

        # -------------------------
        # TEMP
        # -------------------------
        dict(sec_codigo="TEMP", sec_titulo="Temperatura", sec_orden=30,
             item_codigo="TEMP_APLICA", item_nombre="Control de temperatura aplica",
             item_desc="Si no aplica, marcar NA.", requerido=False, critico=False, orden=10),
        dict(sec_codigo="TEMP", sec_titulo="Temperatura", sec_orden=30,
             item_codigo="TEMP_REGISTRO", item_nombre="Temperatura registrada (nota)",
             item_desc="Registra la temperatura en la nota del ítem (si aplica).",
             requerido=False, critico=False, orden=20),
        dict(sec_codigo="TEMP", sec_titulo="Temperatura", sec_orden=30,
             item_codigo="TEMP_CONFORME", item_nombre="Temperatura conforme al rango esperado",
             item_desc="Solo si aplica.", requerido=False, critico=True, orden=30),

        # -------------------------
        # FINAL
        # -------------------------
        dict(sec_codigo="FINAL", sec_titulo="Cierre", sec_orden=99,
             item_codigo="FINAL_NOTA", item_nombre="Observación final (opcional)",
             item_desc=None, requerido=False, critico=False, orden=10),
    ]


def asegurar_plantilla_checklist_simple_v2(db: Session, negocio_id: int) -> InboundChecklistPlantilla:
    """
    Asegura que exista una plantilla activa SIMPLE V2 para el negocio.
    - Si existe una activa, se retorna.
    - Si no existe, se crea con secciones + items seed.
    """
    tpl = (
        db.query(InboundChecklistPlantilla)
        .filter(
            InboundChecklistPlantilla.negocio_id == negocio_id,
            InboundChecklistPlantilla.activo.is_(True),
        )
        .order_by(InboundChecklistPlantilla.id.desc())
        .first()
    )
    if tpl:
        return tpl

    now = utcnow()
    tpl = InboundChecklistPlantilla(
        negocio_id=negocio_id,
        nombre="Checklist SIMPLE ORBION",
        version=1,
        activo=True,
        creado_en=now,
        actualizado_en=now,
    )
    db.add(tpl)
    db.flush()

    seed = _seed_default_simple_v2()

    # crear secciones únicas por codigo
    sec_map: Dict[str, InboundChecklistSeccion] = {}
    for row in seed:
        c = str(row["sec_codigo"])
        if c in sec_map:
            continue
        sec = InboundChecklistSeccion(
            negocio_id=negocio_id,
            plantilla_id=tpl.id,
            codigo=c,
            titulo=str(row["sec_titulo"]),
            orden=int(row["sec_orden"]),
            activo=True,
            creado_en=now,
            actualizado_en=now,
        )
        db.add(sec)
        db.flush()
        sec_map[c] = sec

    # crear items
    for row in seed:
        sec = sec_map[str(row["sec_codigo"])]
        it = InboundChecklistItem(
            negocio_id=negocio_id,
            plantilla_id=tpl.id,
            seccion_id=sec.id,
            codigo=str(row["item_codigo"]),
            nombre=str(row["item_nombre"]),
            descripcion=row.get("item_desc"),
            orden=int(row.get("orden", 0)),
            requerido=bool(row.get("requerido", False)),
            critico=bool(row.get("critico", False)),
            activo=True,
            creado_en=now,
            actualizado_en=now,
        )
        db.add(it)

    db.flush()
    return tpl


def seleccionar_plantilla_para_recepcion(db: Session, negocio_id: int, recepcion: InboundRecepcion) -> InboundChecklistPlantilla:
    """
    SIMPLE V2: por ahora solo selecciona plantilla activa más reciente del negocio.
    """
    tpl = (
        db.query(InboundChecklistPlantilla)
        .filter(
            InboundChecklistPlantilla.negocio_id == negocio_id,
            InboundChecklistPlantilla.activo.is_(True),
        )
        .order_by(InboundChecklistPlantilla.id.desc())
        .first()
    )
    if not tpl:
        tpl = asegurar_plantilla_checklist_simple_v2(db, negocio_id)
    return tpl


# =========================================================
# CORE: crear/obtener ejecución + respuestas seed
# =========================================================

def obtener_o_crear_ejecucion(db: Session, negocio_id: int, recepcion_id: int) -> InboundChecklistEjecucion:
    recepcion = obtener_recepcion_segura(db, negocio_id=negocio_id, recepcion_id=recepcion_id)

    execu = (
        db.query(InboundChecklistEjecucion)
        .filter(
            InboundChecklistEjecucion.negocio_id == negocio_id,
            InboundChecklistEjecucion.recepcion_id == recepcion_id,
        )
        .first()
    )
    if execu:
        return execu

    tpl = seleccionar_plantilla_para_recepcion(db, negocio_id, recepcion)

    now = utcnow()
    execu = InboundChecklistEjecucion(
        negocio_id=negocio_id,
        recepcion_id=recepcion_id,
        plantilla_id=tpl.id,
        creado_en=now,
        actualizado_en=now,
    )
    db.add(execu)
    db.flush()

    # Seed de respuestas por item para que la UI tenga todo “PENDIENTE” desde el inicio
    _seed_respuestas_para_ejecucion(db, negocio_id, recepcion_id, execu.id, tpl.id)

    db.flush()
    return execu


def _seed_respuestas_para_ejecucion(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    ejecucion_id: int,
    plantilla_id: int,
) -> None:
    items = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.plantilla_id == plantilla_id,
            InboundChecklistItem.activo.is_(True),
        )
        .order_by(InboundChecklistItem.orden.asc(), InboundChecklistItem.id.asc())
        .all()
    )
    if not items:
        return

    now = utcnow()

    existentes = (
        db.query(InboundChecklistRespuesta.item_id)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
            InboundChecklistRespuesta.ejecucion_id == ejecucion_id,
        )
        .all()
    )
    existentes_set = {int(x[0]) for x in existentes}

    for it in items:
        if int(it.id) in existentes_set:
            continue
        r = InboundChecklistRespuesta(
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            ejecucion_id=ejecucion_id,
            plantilla_id=plantilla_id,
            item_id=int(it.id),
            estado=EST_PENDIENTE,
            nota=None,
            respondido_por=None,
            respondido_en=None,
            creado_en=now,
            actualizado_en=now,
        )
        db.add(r)


# =========================================================
# VM loader (para template)
# =========================================================

def _cargar_items_plantilla(db: Session, negocio_id: int, plantilla_id: int) -> List[InboundChecklistItem]:
    return (
        db.query(InboundChecklistItem)
        .join(InboundChecklistSeccion, InboundChecklistSeccion.id == InboundChecklistItem.seccion_id)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.plantilla_id == plantilla_id,
            InboundChecklistItem.activo.is_(True),
            InboundChecklistSeccion.activo.is_(True),
        )
        .order_by(
            InboundChecklistSeccion.orden.asc(),
            InboundChecklistSeccion.id.asc(),
            InboundChecklistItem.orden.asc(),
            InboundChecklistItem.id.asc(),
        )
        .all()
    )


def _cargar_secciones_plantilla(db: Session, negocio_id: int, plantilla_id: int) -> Dict[int, InboundChecklistSeccion]:
    rows = (
        db.query(InboundChecklistSeccion)
        .filter(
            InboundChecklistSeccion.negocio_id == negocio_id,
            InboundChecklistSeccion.plantilla_id == plantilla_id,
            InboundChecklistSeccion.activo.is_(True),
        )
        .order_by(InboundChecklistSeccion.orden.asc(), InboundChecklistSeccion.id.asc())
        .all()
    )
    return {int(s.id): s for s in rows}


def _cargar_respuestas(db: Session, negocio_id: int, recepcion_id: int, ejecucion_id: int) -> Dict[int, InboundChecklistRespuesta]:
    rows = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
            InboundChecklistRespuesta.ejecucion_id == ejecucion_id,
        )
        .all()
    )
    return {int(r.item_id): r for r in rows}


def _to_item_vm(
    it: InboundChecklistItem,
    sec: InboundChecklistSeccion,
    r: Optional[InboundChecklistRespuesta],
) -> ChecklistItemVM:
    estado = (r.estado if r else EST_PENDIENTE)
    estado = estado if estado in EST_SET else EST_PENDIENTE

    return ChecklistItemVM(
        item_id=int(it.id),
        seccion_id=int(sec.id),
        seccion_codigo=str(sec.codigo),
        seccion_titulo=str(sec.titulo),
        seccion_orden=int(sec.orden or 0),
        codigo=str(it.codigo),
        nombre=str(it.nombre),
        descripcion=it.descripcion,
        requerido=bool(it.requerido),
        critico=bool(it.critico),
        orden=int(it.orden or 0),
        activo=bool(it.activo),
        estado=str(estado),
        nota=(r.nota if r else None),
        respondido_por=(r.respondido_por if r else None),
        respondido_en=(r.respondido_en if r else None),
        actualizado_en=(r.actualizado_en if r else None),
    )


def obtener_checklist_vm(db: Session, negocio_id: int, recepcion_id: int) -> ChecklistRecepcionVM:
    execu = obtener_o_crear_ejecucion(db, negocio_id, recepcion_id)

    tpl = db.query(InboundChecklistPlantilla).filter(InboundChecklistPlantilla.id == execu.plantilla_id).first()
    if not tpl:
        raise InboundDomainError("Plantilla de checklist no encontrada.")

    secciones_map = _cargar_secciones_plantilla(db, negocio_id, execu.plantilla_id)
    items = _cargar_items_plantilla(db, negocio_id, execu.plantilla_id)
    resp_map = _cargar_respuestas(db, negocio_id, recepcion_id, execu.id)

    vm_items: List[ChecklistItemVM] = []
    total = 0
    respondidos = 0
    no_cumple = 0
    criticos_no_cumple = 0
    requeridos_total = 0
    requeridos_ok = 0

    for it in items:
        sec = secciones_map.get(int(it.seccion_id))
        if not sec:
            continue
        r = resp_map.get(int(it.id))

        total += 1
        if it.requerido:
            requeridos_total += 1

        estado = (r.estado if r else EST_PENDIENTE)
        estado = estado if estado in EST_SET else EST_PENDIENTE

        if _es_respondido(estado, (r.nota if r else None)):
            respondidos += 1

        if estado == EST_NO_CUMPLE:
            no_cumple += 1
            if it.critico:
                criticos_no_cumple += 1

        if it.requerido and estado == EST_CUMPLE:
            requeridos_ok += 1

        vm_items.append(_to_item_vm(it, sec, r))

    progreso = round((respondidos / total) * 100.0, 1) if total > 0 else 0.0
    req_pend = max(requeridos_total - requeridos_ok, 0)

    if criticos_no_cumple > 0:
        sugerido_ui = "BLOQUEADO"
    elif no_cumple > 0:
        sugerido_ui = "APROBADO CON OBS"
    elif requeridos_total > 0 and req_pend == 0:
        sugerido_ui = "APROBADO"
    else:
        sugerido_ui = "PENDIENTE"

    resumen = {
        "items_total": total,
        "items_respondidos": respondidos,
        "progreso_pct": progreso,
        "requeridos_total": requeridos_total,
        "requeridos_ok": requeridos_ok,
        "requeridos_pendientes": req_pend,
        "no_cumple": no_cumple,
        "criticos_no_cumple": criticos_no_cumple,
        "resultado_sugerido_ui": sugerido_ui,
        "bloqueado": bool(criticos_no_cumple > 0),
    }

    secciones = _build_secciones(vm_items)

    return ChecklistRecepcionVM(
        recepcion_id=recepcion_id,
        ejecucion_id=int(execu.id),
        plantilla_id=int(execu.plantilla_id),
        plantilla_nombre=str(tpl.nombre),
        creado_en=execu.creado_en or utcnow(),
        actualizado_en=execu.actualizado_en or utcnow(),
        items=vm_items,
        secciones=secciones,
        resumen=resumen,
    )


# =========================================================
# GUARDAR (autosave item)
# =========================================================

def guardar_respuesta_item(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    item_id: int,
    *,
    estado: Optional[str],
    nota: Optional[str],
    respondido_por: Optional[str],
) -> InboundChecklistRespuesta:
    execu = obtener_o_crear_ejecucion(db, negocio_id, recepcion_id)

    it = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.plantilla_id == execu.plantilla_id,
            InboundChecklistItem.id == item_id,
            InboundChecklistItem.activo.is_(True),
        )
        .first()
    )
    if not it:
        raise InboundDomainError("Ítem inválido para la plantilla aplicada.")

    now = utcnow()
    actor = _s(respondido_por) or None
    nota_clean = _s(nota) or None

    estado_clean = _estado_normalize(estado)
    if estado_clean not in EST_SET:
        estado_clean = EST_PENDIENTE

    resp = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion_id,
            InboundChecklistRespuesta.ejecucion_id == execu.id,
            InboundChecklistRespuesta.item_id == item_id,
        )
        .first()
    )

    # respondido_en: solo si estado != PENDIENTE (nota no afecta)
    responded_ts = (now if (estado_clean != EST_PENDIENTE) else None)

    if resp is None:
        resp = InboundChecklistRespuesta(
            negocio_id=negocio_id,
            recepcion_id=recepcion_id,
            ejecucion_id=execu.id,
            plantilla_id=execu.plantilla_id,
            item_id=item_id,
            estado=estado_clean,
            nota=nota_clean,
            respondido_por=actor,
            respondido_en=responded_ts,
            creado_en=now,
            actualizado_en=now,
        )
        db.add(resp)
    else:
        resp.estado = estado_clean
        resp.nota = nota_clean
        resp.respondido_por = actor
        resp.respondido_en = responded_ts
        resp.actualizado_en = now

    # =========================================================
    # ENTERPRISE RULE: TEMP_APLICA = NA => auto-NA resto TEMP_*
    # =========================================================
    if str(it.codigo or "").strip().upper() == "TEMP_APLICA" and estado_clean == EST_NA:
        temp_items = (
            db.query(InboundChecklistItem)
            .join(InboundChecklistSeccion, InboundChecklistSeccion.id == InboundChecklistItem.seccion_id)
            .filter(
                InboundChecklistItem.negocio_id == negocio_id,
                InboundChecklistItem.plantilla_id == execu.plantilla_id,
                InboundChecklistItem.activo.is_(True),
                InboundChecklistSeccion.activo.is_(True),
                InboundChecklistSeccion.codigo == "TEMP",
            )
            .all()
        )

        temp_ids = [
            int(x.id) for x in temp_items
            if str(getattr(x, "codigo", "") or "").strip().upper() != "TEMP_APLICA"
        ]

        if temp_ids:
            existing = (
                db.query(InboundChecklistRespuesta)
                .filter(
                    InboundChecklistRespuesta.negocio_id == negocio_id,
                    InboundChecklistRespuesta.recepcion_id == recepcion_id,
                    InboundChecklistRespuesta.ejecucion_id == execu.id,
                    InboundChecklistRespuesta.item_id.in_(temp_ids),
                )
                .all()
            )
            by_item = {int(r.item_id): r for r in existing}

            for tid in temp_ids:
                r2 = by_item.get(tid)
                if r2 is None:
                    r2 = InboundChecklistRespuesta(
                        negocio_id=negocio_id,
                        recepcion_id=recepcion_id,
                        ejecucion_id=execu.id,
                        plantilla_id=execu.plantilla_id,
                        item_id=tid,
                        estado=EST_NA,
                        nota=None,
                        respondido_por=actor,
                        respondido_en=now,
                        creado_en=now,
                        actualizado_en=now,
                    )
                    db.add(r2)
                else:
                    r2.estado = EST_NA
                    r2.respondido_por = actor
                    r2.respondido_en = now
                    r2.actualizado_en = now

    execu.actualizado_en = now
    db.flush()
    return resp
