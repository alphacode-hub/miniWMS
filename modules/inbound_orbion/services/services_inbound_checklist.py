# modules/inbound_orbion/services/services_inbound_checklist.py

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from core.models import (
    InboundChecklistItem,
    InboundChecklistRespuesta,
    InboundRecepcion,
)
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


# ============================
#   ITEMS CHECKLIST (CONFIG)
# ============================

def crear_checklist_item_inbound(
    db: Session,
    negocio_id: int,
    texto: str,
    orden: int | None = None,
    activo: bool = True,
) -> InboundChecklistItem:
    if not texto or not texto.strip():
        raise InboundDomainError("El texto del ítem de checklist es obligatorio.")

    if orden is None:
        # Calcular siguiente orden
        max_orden = (
            db.query(InboundChecklistItem)
            .filter(InboundChecklistItem.negocio_id == negocio_id)
            .order_by(InboundChecklistItem.orden.desc())
            .first()
        )
        orden = (max_orden.orden + 1) if max_orden else 1

    item = InboundChecklistItem(
        negocio_id=negocio_id,
        texto=texto.strip(),
        orden=orden,
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
        if not texto.strip():
            raise InboundDomainError("El texto del ítem de checklist no puede estar vacío.")
        item.texto = texto.strip()

    if orden is not None:
        item.orden = orden

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
    q = db.query(InboundChecklistItem).filter(
        InboundChecklistItem.negocio_id == negocio_id,
    )
    if solo_activos:
        q = q.filter(InboundChecklistItem.activo.is_(True))
    return q.order_by(InboundChecklistItem.orden.asc()).all()


# ============================
#   RESPUESTAS POR RECEPCIÓN
# ============================

def registrar_respuestas_checklist_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    respuestas: Iterable[dict],
    respondido_por_id: int | None = None,
) -> None:
    """
    respuestas: iterable de dicts con:
        { "item_id": int, "valor_bool": bool | None, "comentario": str | None }
    """
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    ahora = datetime.utcnow()

    for r in respuestas:
        item_id = int(r.get("item_id"))
        valor_bool = r.get("valor_bool")
        comentario = r.get("comentario")

        item = db.get(InboundChecklistItem, item_id)
        if not item or item.negocio_id != negocio_id:
            raise InboundDomainError(
                f"Ítem de checklist {item_id} no pertenece a este negocio."
            )

        respuesta = (
            db.query(InboundChecklistRespuesta)
            .filter(
                InboundChecklistRespuesta.negocio_id == negocio_id,
                InboundChecklistRespuesta.recepcion_id == recepcion.id,
                InboundChecklistRespuesta.item_id == item.id,
            )
            .first()
        )

        if not respuesta:
            respuesta = InboundChecklistRespuesta(
                negocio_id=negocio_id,
                recepcion_id=recepcion.id,
                item_id=item.id,
            )
            db.add(respuesta)

        respuesta.valor_bool = valor_bool
        respuesta.comentario = (comentario or "").strip() or None
        respuesta.respondido_por_id = respondido_por_id
        respuesta.respondido_en = ahora

    # Marcar flag de checklist completado en la recepción
    recepcion.checklist_completado = True
    recepcion.checklist_completado_por_id = respondido_por_id
    recepcion.checklist_completado_en = ahora

    db.commit()


def obtener_respuestas_checklist_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
) -> list[InboundChecklistRespuesta]:
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    return (
        db.query(InboundChecklistRespuesta)
        .filter(
            InboundChecklistRespuesta.negocio_id == negocio_id,
            InboundChecklistRespuesta.recepcion_id == recepcion.id,
        )
        .all()
    )
