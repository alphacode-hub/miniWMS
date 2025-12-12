# services_alerts.py
from __future__ import annotations

from datetime import datetime, date, timedelta
import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import Negocio, Producto, Movimiento, Alerta


def crear_alerta_interna(
    db: Session,
    negocio_id: int,
    tipo: str,
    mensaje: str,
    origen: str = "sistema",
    datos: dict | None = None,
    destino: str | None = None,
) -> Alerta:
    """
    Crea una alerta interna para un negocio.

    - Evita duplicar EXACTAMENTE el mismo mensaje para el mismo negocio/tipo
      en las últimas 24 horas (para no spamear).
    - Guarda detalles opcionales en datos_json como JSON.
    """
    ahora = datetime.utcnow()
    hace_24h = ahora - timedelta(hours=24)

    # Evitar spam: misma alerta, mismo mensaje en menos de 24h
    alerta_existente = (
        db.query(Alerta)
        .filter(
            Alerta.negocio_id == negocio_id,
            Alerta.tipo == tipo,
            Alerta.mensaje == mensaje,
            Alerta.fecha_creacion >= hace_24h,
        )
        .first()
    )

    if alerta_existente:
        return alerta_existente

    datos_json = json.dumps(datos, ensure_ascii=False) if datos else None

    alerta = Alerta(
        negocio_id=negocio_id,
        tipo=tipo,
        mensaje=mensaje,
        destino=destino,
        estado="pendiente",  # flujo interno: pendiente → leida / enviada
        fecha_creacion=ahora,
        fecha_envio=None,
        origen=origen,
        datos_json=datos_json,
    )
    db.add(alerta)
    db.commit()
    db.refresh(alerta)

    # Debug opcional:
    # print(f"[ALERTAS] Creada alerta tipo={tipo} negocio_id={negocio_id}: {mensaje}")

    return alerta


def evaluar_alertas_stock(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
    motivo: str | None = None,
) -> None:
    """
    Evalúa si el producto está en stock crítico o sobre-stock
    y genera alertas internas en base a stock_min / stock_max.

    Se llama después de entradas/salidas/ajustes.
    """
    negocio_id = user["negocio_id"]

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        return

    # Producto del negocio por negocio_id
    producto = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            func.lower(Producto.nombre) == producto_nombre.lower(),
        )
        .first()
    )
    if not producto:
        return

    # Si no tiene reglas, no genera alertas
    if producto.stock_min is None and producto.stock_max is None:
        return

    # Calcular stock total del producto en el negocio (entradas - salidas/ajustes negativos)
    movimientos = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            func.lower(Movimiento.producto) == producto_nombre.lower(),
        )
        .all()
    )

    stock_total = 0
    for m in movimientos:
        qty = m.cantidad or 0
        if m.tipo == "salida" or (m.tipo == "ajuste" and qty < 0):
            stock_total -= abs(qty)
        else:
            stock_total += abs(qty)

    destino = motivo

    # Debug opcional:
    # print(
    #     f"[ALERTAS_STOCK] producto='{producto.nombre}' "
    #     f"negocio_id={negocio_id} stock_total={stock_total} "
    #     f"min={producto.stock_min} max={producto.stock_max}"
    # )

    # 🔴 Alerta por stock mínimo
    if producto.stock_min is not None and stock_total < producto.stock_min:
        mensaje = (
            f"Stock CRÍTICO de '{producto.nombre}': {stock_total} unidades "
            f"(mínimo configurado: {producto.stock_min})."
        )
        crear_alerta_interna(
            db=db,
            negocio_id=negocio_id,
            tipo="stock_min",
            mensaje=mensaje,
            origen=origen,
            destino=destino,
            datos={
                "producto": producto.nombre,
                "stock_total": stock_total,
                "stock_min": producto.stock_min,
                "motivo": motivo,
            },
        )

    # 🟠 Alerta por sobre-stock
    if producto.stock_max is not None and stock_total > producto.stock_max:
        mensaje = (
            f"SOBRE-STOCK de '{producto.nombre}': {stock_total} unidades "
            f"(máximo configurado: {producto.stock_max})."
        )
        crear_alerta_interna(
            db=db,
            negocio_id=negocio_id,
            tipo="stock_max",
            mensaje=mensaje,
            origen=origen,
            destino=destino,
            datos={
                "producto": producto.nombre,
                "stock_total": stock_total,
                "stock_max": producto.stock_max,
                "motivo": motivo,
            },
        )


def evaluar_alertas_vencimiento(
    db: Session,
    user: dict,
    producto_nombre: str,
    origen: str,
) -> None:
    """
    Genera alertas internas cuando un producto está vencido o próximo a vencer.
    Se basa en las fechas de vencimiento registradas en las ENTRADAS.
    """
    negocio_id = user["negocio_id"]

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        return

    # Filtrar ENTRADAS del producto con fecha de vencimiento válida
    entradas = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            Movimiento.tipo == "entrada",
            func.lower(Movimiento.producto) == producto_nombre.lower(),
            Movimiento.fecha_vencimiento.isnot(None),
        )
        .all()
    )

    if not entradas:
        return

    hoy = date.today()
    destino = "vencimiento"

    for e in entradas:
        fv = e.fecha_vencimiento
        if not fv:
            continue

        dias = (fv - hoy).days  # días restantes

        # 🔴 Producto ya vencido
        if dias < 0:
            mensaje = (
                f"ALERTA: El producto '{e.producto}' está VENCIDO "
                f"(fecha: {fv.strftime('%d-%m-%Y')})."
            )
            crear_alerta_interna(
                db=db,
                negocio_id=negocio_id,
                tipo="vencido",
                mensaje=mensaje,
                origen=origen,
                destino=destino,
                datos={
                    "producto": e.producto,
                    "fecha_vencimiento": fv.isoformat(),
                    "dias_restantes": dias,
                },
            )
            continue

        # 🟠 Próximo a vencer (dentro de 7 días)
        if dias <= 7:
            mensaje = (
                f"Advertencia: El producto '{e.producto}' vencerá en {dias} días "
                f"(fecha: {fv.strftime('%d-%m-%Y')})."
            )
            crear_alerta_interna(
                db=db,
                negocio_id=negocio_id,
                tipo="proximo_vencer",
                mensaje=mensaje,
                origen=origen,
                destino=destino,
                datos={
                    "producto": e.producto,
                    "fecha_vencimiento": fv.isoformat(),
                    "dias_restantes": dias,
                },
            )
