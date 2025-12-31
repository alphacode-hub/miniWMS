# modules/inbound_orbion/services/services_inbound_citas_sync.py
from __future__ import annotations

from sqlalchemy.orm import Session

from core.models.enums import RecepcionEstado, CitaEstado
from core.models.inbound.citas import InboundCita


def _set_enum_by_name(obj, attr: str, name: str) -> None:
    """
    Setter robusto para atributos Enum:
    - Si el atributo existe y es Enum, setea por NAME.
    - Si no existe o no es Enum, no rompe (best-effort).
    """
    if not hasattr(obj, attr):
        return

    cur = getattr(obj, attr, None)

    # Si cur ya es Enum => usamos su clase
    enum_cls = type(cur) if cur is not None else None
    members = getattr(enum_cls, "__members__", None) if enum_cls else None

    # Si no podemos inferir clase desde cur (cur None),
    # intentamos inferir desde el tipo del value actual del modelo, si existe metadata.
    if not members:
        # fallback: si el attr está anotado como Enum, podría igual funcionar por assignment directo
        try:
            setattr(obj, attr, name)  # si fuera string en algún modelo viejo
        except Exception:
            pass
        return

    if isinstance(members, dict) and name in members:
        setattr(obj, attr, members[name])


def estado_cita_desde_recepcion(estado_recepcion: RecepcionEstado) -> CitaEstado:
    """
    Regla oficial (enterprise) RECEPCIÓN -> CITA

    PRE_REGISTRADO                     => PROGRAMADA
    EN_ESPERA / EN_DESCARGA /
    EN_CONTROL_CALIDAD                 => ARRIBADO
    CERRADO                            => COMPLETADA
    CANCELADO                          => CANCELADA
    """
    if estado_recepcion == RecepcionEstado.CANCELADO:
        return CitaEstado.CANCELADA

    if estado_recepcion == RecepcionEstado.CERRADO:
        return CitaEstado.COMPLETADA

    if estado_recepcion in (
        RecepcionEstado.EN_ESPERA,
        RecepcionEstado.EN_DESCARGA,
        RecepcionEstado.EN_CONTROL_CALIDAD,
    ):
        return CitaEstado.ARRIBADO

    return CitaEstado.PROGRAMADA


def sync_cita_desde_recepcion(db: Session, recepcion) -> None:
    """
    Sincroniza la CITA asociada a la RECEPCIÓN.

    ✅ Importante:
    - En tu modelo, la FK está en Recepción (recepcion.cita_id)
    - InboundCita NO tiene recepcion_id
    - Este sync NO hace commit (el caller decide la transacción)
    """

    # 1) Resolver cita por relación (si está cargada)
    cita = getattr(recepcion, "cita", None)

    # 2) Si no viene cargada, resolver por cita_id
    if cita is None:
        cita_id = getattr(recepcion, "cita_id", None)
        if not cita_id:
            return
        cita = db.get(InboundCita, int(cita_id))
        if not cita:
            return

    # 3) Multi-tenant safety (si por alguna razón hay mismatch)
    rec_negocio_id = getattr(recepcion, "negocio_id", None)
    cita_negocio_id = getattr(cita, "negocio_id", None)
    if rec_negocio_id is not None and cita_negocio_id is not None:
        if int(rec_negocio_id) != int(cita_negocio_id):
            return

    # 4) Estado recepción (Enum)
    estado_rec = getattr(recepcion, "estado", None)
    if estado_rec is None:
        return

    # 5) Mapear a estado de cita
    nuevo_estado_cita = estado_cita_desde_recepcion(estado_rec)

    # 6) Aplicar si cambia
    if getattr(cita, "estado", None) != nuevo_estado_cita:
        cita.estado = nuevo_estado_cita

    # 7) Persistir en sesión (sin commit)
    db.add(cita)
    db.flush()  # asegura que quede aplicado dentro de la misma transacción
