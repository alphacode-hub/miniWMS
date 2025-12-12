# modules/inbound_orbion/services/services_inbound_fotos.py

from __future__ import annotations

from sqlalchemy.orm import Session

from core.models import InboundFoto, InboundLinea, InboundIncidencia
from .services_inbound_core import (
    InboundDomainError,
    obtener_recepcion_segura,
)


def crear_foto_inbound(
    db: Session,
    negocio_id: int,
    recepcion_id: int,
    ruta_archivo: str,
    tipo: str | None = None,
    descripcion: str | None = None,
    mime_type: str | None = None,
    subido_por_id: int | None = None,
    linea_id: int | None = None,
    incidencia_id: int | None = None,
) -> InboundFoto:
    """
    Crea una foto/evidencia asociada a una recepción, y opcionalmente
    a una línea o una incidencia.

    La relación con linea/incidencia sólo se aplicará si el modelo tiene
    esos campos definidos (se verifica con hasattr para ser robustos).
    """
    recepcion = obtener_recepcion_segura(db, recepcion_id, negocio_id)

    if not ruta_archivo:
        raise InboundDomainError("La ruta del archivo de la foto es obligatoria.")

    foto = InboundFoto(
        negocio_id=negocio_id,
        recepcion_id=recepcion.id,
        ruta_archivo=ruta_archivo,
        tipo=(tipo or "").strip().upper() or None,
        descripcion=(descripcion or "").strip() or None,
        mime_type=mime_type,
        subido_por_id=subido_por_id,
    )

    # Asociar a línea (si el modelo lo soporta)
    if linea_id is not None and hasattr(InboundFoto, "linea_id"):
        linea = db.get(InboundLinea, linea_id)
        if not linea:
            raise InboundDomainError("Línea inbound asociada a la foto no existe.")
        if linea.recepcion_id != recepcion.id:
            raise InboundDomainError(
                "La línea asociada a la foto no pertenece a esta recepción."
            )
        foto.linea_id = linea.id

    # Asociar a incidencia (si el modelo lo soporta)
    if incidencia_id is not None and hasattr(InboundFoto, "incidencia_id"):
        incidencia = db.get(InboundIncidencia, incidencia_id)
        if not incidencia:
            raise InboundDomainError("Incidencia inbound asociada a la foto no existe.")
        if incidencia.recepcion_id != recepcion.id:
            raise InboundDomainError(
                "La incidencia asociada a la foto no pertenece a esta recepción."
            )
        foto.incidencia_id = incidencia.id

    db.add(foto)
    db.commit()
    db.refresh(foto)
    return foto


def eliminar_foto_inbound(
    db: Session,
    negocio_id: int,
    foto_id: int,
) -> None:
    foto = db.get(InboundFoto, foto_id)
    if not foto or foto.negocio_id != negocio_id:
        raise InboundDomainError("Foto inbound no encontrada para este negocio.")

    db.delete(foto)
    db.commit()
