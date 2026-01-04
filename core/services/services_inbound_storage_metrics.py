# core/services/services_inbound_storage_metrics.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models.inbound.documentos import InboundDocumento
from core.models import InboundFoto


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def storage_activo_bytes_inbound(db: Session, *, negocio_id: int) -> int:
    """
    Storage ACTIVO (real) para INBOUND:
    - Documentos activos y no borrados
    - Fotos activas

    Ojo: esto NO depende de usage counters (billing).
    """
    docs_stmt = (
        select(func.coalesce(func.sum(InboundDocumento.size_bytes), 0))
        .where(InboundDocumento.negocio_id == int(negocio_id))
        .where(InboundDocumento.activo == 1)
        .where(InboundDocumento.is_deleted.is_(False))
    )

    fotos_stmt = (
        select(func.coalesce(func.sum(InboundFoto.size_bytes), 0))
        .where(InboundFoto.negocio_id == int(negocio_id))
        .where(InboundFoto.activo == 1)
    )

    docs_bytes = _to_int(db.execute(docs_stmt).scalar_one_or_none())
    fotos_bytes = _to_int(db.execute(fotos_stmt).scalar_one_or_none())
    return docs_bytes + fotos_bytes


def storage_activo_mb_inbound(db: Session, *, negocio_id: int) -> float:
    return float(storage_activo_bytes_inbound(db, negocio_id=negocio_id)) / (1024.0 * 1024.0)
