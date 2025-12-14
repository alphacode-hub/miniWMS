# modules/inbound_orbion/services/services_inbound_checklist.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models.inbound import (
    InboundChecklistItem,
    InboundChecklistRespuesta,
    InboundRecepcion,
)
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


# =========================================================
# TIME
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =========================================================
# INPUT TYPES
# =========================================================

@dataclass(frozen=True)
class ChecklistRespuestaInput:
    item_id: int
    valor_bool: Optional[bool] = None
    comentario: Optional[str] = None


def _as_input(r: dict[str, Any]) -> ChecklistRespuestaInput:
    if "item_id" not in r:
        raise InboundDomainError("Cada respuesta debe incluir item_id.")
    try:
        item_id = int(r.get("item_id"))
    except Exception:
        raise InboundDomainError("item_id inválido en respuestas de checklist.")

    valor_bool = r.get("valor_bool")
    if valor_bool is not None and not isinstance(valor_bool, bool):
        # Aceptamos 0/1 de forms si viene así
        if str(valor_bool).strip() in {"0", "1"}:
            valor_bool = str(valor_bool).strip() == "1"
        else:
            raise InboundDomainError("valor_bool debe ser booleano (true/false).")

    comentario = r.get("comentario")
    comentario = (str(comentario).strip() if comentario is not None else None) or None

    return ChecklistRespuestaInput(item_id=item_id, valor_bool=valor_bool, comentario=comentario)


# =========================================================
# ITEMS CHECKLIST (CONFIG)
# =========================================================

def crear_checklist_item_inbound(
    db: Session,
    negocio_id: int,
    texto: str,
    orden: int | None = None,
    activo: bool = True,
) -> InboundChecklistItem:
    texto_norm = (texto or "").strip()
    if not texto_norm:
        raise InboundDomainError("El texto del ítem de checklist es obligatorio.")

    if orden is None:
        # Siguiente orden por negocio (SQL friendly)
        max_orden = (
            db.query(func.max(InboundChecklistItem.orden))
            .filter(InboundChecklistItem.negocio_id == negocio_id)
            .scalar()
        )
        orden = int((max_orden or 0) + 1)

    item = InboundChecklistItem(
        negocio_id=negocio_id,
        texto=texto_norm,
        orden=int(orden),
        activo=bool(activo),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def actualizar_checklist_item_inbound(
    db: Session,
    negocio_id: int,
    item_id: int,
    texto: str | None = None,
    orden: int | None = None,
    activo: bool | None = None,
) -> InboundChecklistItem:
    item = db.get(InboundChecklistItem, item_id)
    if not item or item.negocio_id != negocio_id:
        raise InboundDomainError("Ítem de checklist inbound no encontrado.")

    if texto is not None:
        texto_norm = texto.strip()
        if not texto_norm:
            raise InboundDomainError("El texto del ítem de checklist no puede estar vacío.")
        item.texto = texto_norm

    if orden is not None:
        item.orden = int(orden)

    if activo is not None:
        item.activo = bool(activo)

    db.commit()
    db.refresh(item)
    return item


def listar_checklist_items_inbound(
    db: Session,
    negocio_id: int,
    solo_activos: bool = True,
) -> list[InboundChecklistItem]:
    q = db.query(InboundChecklistItem).filter(InboundChecklistItem.negocio_id == negocio_id)
    if solo_activos:
        q = q.filter(InboundChecklistItem.activo.is_(True))
    return q.order_by(InboundChecklistItem.orden.asc(), InboundChecklistItem.id.asc()).all()


# =========================================================
# RESPUESTAS POR RECEPCIÓN
# =========================================================

def registrar_respuestas_checklist_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    respuestas: Iterable[dict[str, Any]],
    respondido_por_id: int | None = None,
) -> None:
    """
    Registra o actualiza respuestas de checklist para una recepción.

    respuestas: iterable de dicts con:
        {
            "item_id": int,
            "valor_bool": bool | 0/1 | None,
            "comentario": str | None,
        }

    ✅ Enterprise:
    - Valida pertenencia de items al negocio
    - Idempotente (upsert lógico)
    - Transaccional (un commit)
    - Marca checklist_completado en la recepción
    """
    recepcion: InboundRecepcion = obtener_recepcion_segura(
        db=db,
        recepcion_id=recepcion_id,
        negocio_id=negocio_id,
    )

    respuestas_in = [_as_input(r) for r in respuestas]
    if not respuestas_in:
        raise InboundDomainError("No se recibieron respuestas de checklist.")

    item_ids = {ri.item_id for ri in respuestas_in}

    items = (
        db.query(InboundChecklistItem)
        .filter(
            InboundChecklistItem.negocio_id == negocio_id,
            InboundChecklistItem.id.in_(item_ids),
        )
        .all()
    )
    found_ids = {it.id for it in items}
    missing = sorted(item_ids - found_ids)
    if missing:
        raise InboundDomainError(f"Ítems de checklist inválidos para el negocio: {missing}")

    # Mapear respuestas existentes para evitar N queries
    existentes = (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion.id,
            InboundChecklistRespuesta.item_id.in_(item_ids),
        )
        .all()
    )
    existentes_map = {e.item_id: e for e in existentes}

    ahora = utcnow()

    for ri in respuestas_in:
        respuesta = existentes_map.get(ri.item_id)
        if respuesta is None:
            respuesta = InboundChecklistRespuesta(
                negocio_id=negocio_id,
                recepcion_id=recepcion.id,
                item_id=ri.item_id,
            )
            db.add(respuesta)

        respuesta.valor_bool = ri.valor_bool
        respuesta.valor_texto = ri.comentario
        # valor_numerico / ruta_foto quedan para futuros usos
        respuesta.respondido_por_id = respondido_por_id
        respuesta.respondido_en = ahora

    # Flag en recepción
    recepcion.checklist_completado = True
    recepcion.checklist_completado_por_id = respondido_por_id
    recepcion.checklist_completado_en = ahora

    db.commit()


def obtener_respuestas_checklist_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
) -> list[InboundChecklistRespuesta]:
    recepcion = obtener_recepcion_segura(
        db=db,
        recepcion_id=recepcion_id,
        negocio_id=negocio_id,
    )

    return (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion.id,
        )
        .order_by(InboundChecklistRespuesta.id.asc())
        .all()
    )
