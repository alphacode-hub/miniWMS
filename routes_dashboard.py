# routes_dashboard.py
import json
from datetime import datetime, timedelta, date
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Producto, Movimiento, Zona, Slot, Ubicacion, Alerta
from security import require_user_dep, is_superadmin


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER DASHBOARD
# ============================

router = APIRouter(
    prefix="",
    tags=["dashboard"],
)


# ============================
#     DASHBOARD
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user_dep),
):
    """
    Dashboard principal del negocio:
    - Admin / Operador: ven este dashboard de negocio.
    - Superadmin: es redirigido al dashboard corporativo.
    """
    if user.get("rol") == "superadmin":
        return RedirectResponse("/superadmin/dashboard", status_code=302)

    hoy = date.today()
    negocio_id = user["negocio_id"]

    # ============================
    # 1) Productos
    # ============================

    productos = (
        db.query(Producto)
        .filter(Producto.negocio_id == negocio_id)
        .order_by(Producto.nombre.asc())
        .all()
    )
    productos_by_name = {p.nombre.lower(): p for p in productos}
    total_skus = len(productos)

    # ============================
    # 2) Movimientos
    # ============================

    movimientos_all = (
        db.query(Movimiento)
        .filter(Movimiento.negocio_id == negocio_id)
        .order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        .all()
    )

    # Flags onboarding
    tiene_zonas = (
        db.query(Zona)
        .filter(Zona.negocio_id == negocio_id)
        .count() > 0
    )

    tiene_slots = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Zona.negocio_id == negocio_id)
        .count() > 0
    )

    tiene_productos = total_skus > 0
    tiene_movimientos = len(movimientos_all) > 0

    # ============================
    # 3) Totales por producto y lotes (FEFO simplificado)
    # ============================

    totales_producto = {}            # "producto" -> qty total
    lotes_por_producto = {}          # "producto" -> [{fv, qty}]

    for mov in movimientos_all:
        prod_name = mov.producto or ""
        prod_key = prod_name.lower()
        if not prod_key:
            continue

        qty = mov.cantidad or 0

        # Salidas/ajustes negativos restan
        if mov.tipo == "salida" or (mov.tipo == "ajuste" and qty < 0):
            signed_delta = -abs(qty)
        else:
            signed_delta = abs(qty)

        # total por producto
        totales_producto[prod_key] = totales_producto.get(prod_key, 0) + signed_delta

        # lotes
        if signed_delta > 0:
            fv = mov.fecha_vencimiento
            lotes_por_producto.setdefault(prod_name, []).append({"fv": fv, "qty": signed_delta})

        elif signed_delta < 0:
            # FEFO
            lotes = lotes_por_producto.setdefault(prod_name, [])
            qty_to_remove = -signed_delta

            lotes.sort(
                key=lambda l: (
                    l["fv"] is None,
                    l["fv"] or date(9999, 12, 31),
                )
            )

            for lote in lotes:
                if qty_to_remove <= 0:
                    break
                disp = lote["qty"]
                if disp <= 0:
                    continue
                usar = min(disp, qty_to_remove)
                lote["qty"] -= usar
                qty_to_remove -= usar

            lotes[:] = [l for l in lotes if l["qty"] > 0]

    # ============================
    # 4) Resumen de estados por producto
    # ============================

    resumen_stock = {"Crítico": 0, "OK": 0, "Sobre-stock": 0, "Sin configuración": 0}
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}
    estado_producto = {}

    for p in productos:
        key = p.nombre.lower()
        stock_total = totales_producto.get(key, 0)
        stock_min = p.stock_min
        stock_max = p.stock_max

        if stock_min is None and stock_max is None:
            est = "Sin configuración"
        else:
            if stock_min is not None and stock_total < stock_min:
                est = "Crítico"
            elif stock_max is not None and stock_total > stock_max:
                est = "Sobre-stock"
            else:
                est = "OK"

        prev = estado_producto.get(p.nombre)
        if prev is None or prioridad[est] > prioridad.get(prev, 0):
            estado_producto[p.nombre] = est

    for est in estado_producto.values():
        resumen_stock[est] += 1

    # ============================
    # 5) Resumen vencimientos
    # ============================

    resumen_venc = {"Vencido": 0, "<7": 0, "<15": 0, "<30": 0, "<60": 0, "Normal": 0, "Sin fecha": 0}

    for prod_name, lotes in lotes_por_producto.items():
        fv_min = None
        for l in lotes:
            if l["fv"] is not None:
                if fv_min is None or l["fv"] < fv_min:
                    fv_min = l["fv"]

        if fv_min is None:
            resumen_venc["Sin fecha"] += 1
        else:
            dias = (fv_min - hoy).days
            if dias < 0:
                resumen_venc["Vencido"] += 1
            elif dias <= 7:
                resumen_venc["<7"] += 1
            elif dias <= 15:
                resumen_venc["<15"] += 1
            elif dias <= 30:
                resumen_venc["<30"] += 1
            elif dias <= 60:
                resumen_venc["<60"] += 1
            else:
                resumen_venc["Normal"] += 1

    # ============================
    # 6) Total unidades en stock + valor inventario
    # ============================

    total_unidades = sum(q for q in totales_producto.values() if q > 0)

    valor_inventario = 0.0
    for prod_key, qty in totales_producto.items():
        if qty <= 0:
            continue
        p = productos_by_name.get(prod_key)
        if p and p.costo_unitario is not None:
            valor_inventario += qty * p.costo_unitario

    valor_inventario = int(round(valor_inventario))

    # ============================
    # 7) Pérdidas por merma últimos 30 días
    # ============================

    desde_30 = hoy - timedelta(days=30)

    salidas_merma = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            Movimiento.tipo == "salida",
            Movimiento.motivo_salida == "merma",
            Movimiento.fecha >= datetime.combine(desde_30, datetime.min.time()),
        )
        .all()
    )

    perdidas_merma_30d = 0.0
    for m in salidas_merma:
        p = productos_by_name.get((m.producto or "").lower())
        if p and p.costo_unitario is not None:
            perdidas_merma_30d += abs(m.cantidad or 0) * p.costo_unitario

    perdidas_merma_30d = int(round(perdidas_merma_30d))

    # ============================
    # 8) Últimos 5 movimientos
    # ============================

    movimientos_recientes = (
        db.query(Movimiento)
        .filter(Movimiento.negocio_id == negocio_id)
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(5)
        .all()
    )

    # ============================
    # 9) Gráfico últimos 7 días
    # ============================

    hace_7 = datetime.utcnow() - timedelta(days=7)

    mov_ultimos_7 = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            Movimiento.fecha >= hace_7,
        )
        .order_by(Movimiento.fecha.asc())
        .all()
    )

    serie_por_dia = {}

    for m in mov_ultimos_7:
        dia = m.fecha.date()
        if dia not in serie_por_dia:
            serie_por_dia[dia] = {"entrada": 0, "salida": 0}

        if m.tipo == "salida":
            serie_por_dia[dia]["salida"] += m.cantidad or 0
        else:
            serie_por_dia[dia]["entrada"] += m.cantidad or 0

    dias_ordenados = sorted(serie_por_dia.keys())
    chart_labels = [d.strftime("%d-%m") for d in dias_ordenados]
    chart_entradas = [serie_por_dia[d]["entrada"] for d in dias_ordenados]
    chart_salidas = [serie_por_dia[d]["salida"] for d in dias_ordenados]

    chart_data = {
        "labels": chart_labels,
        "entradas": chart_entradas,
        "salidas": chart_salidas,
    }

    # ============================
    # 10) Alertas pendientes
    # ============================

    alertas_pendientes = (
        db.query(Alerta)
        .filter(
            Alerta.negocio_id == negocio_id,
            Alerta.estado == "pendiente",
        )
        .count()
    )

    # ============================
    # Render
    # ============================

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "total_skus": total_skus,
            "total_unidades": total_unidades,
            "resumen_stock": resumen_stock,
            "resumen_venc": resumen_venc,
            "movimientos_recientes": movimientos_recientes,
            "chart_data_json": json.dumps(chart_data),
            "valor_inventario": valor_inventario,
            "perdidas_merma_30d": perdidas_merma_30d,
            "alertas_pendientes": alertas_pendientes,
            # onboarding flags
            "tiene_zonas": tiene_zonas,
            "tiene_slots": tiene_slots,
            "tiene_productos": tiene_productos,
            "tiene_movimientos": tiene_movimientos,
        },
    )
