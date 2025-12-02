# routes_dashboard.py
import json
from datetime import datetime, timedelta, date
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Producto, Movimiento, Zona, Slot, Ubicacion, Alerta
from security import require_user_dep

# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
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
    # 1) Productos del negocio
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
    # 2) Movimientos del negocio
    # ============================

    movimientos_all = (
        db.query(Movimiento)
        .filter(Movimiento.negocio_id == negocio_id)
        .order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        .all()
    )

    # ============================
    # 3) Flags de onboarding
    # ============================

    # Zonas
    total_zonas = (
        db.query(Zona)
        .filter(Zona.negocio_id == negocio_id)
        .count()
    )
    # Slots
    total_slots = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Zona.negocio_id == negocio_id)
        .count()
    )

    # Flags de faltante (como los usa el template)
    faltan_zonas = (total_zonas == 0)
    faltan_slots = (total_slots == 0)
    faltan_productos = (total_skus == 0)
    faltan_movimientos = (len(movimientos_all) == 0)

    onboarding_completo = not (
        faltan_zonas
        or faltan_slots
        or faltan_productos
        or faltan_movimientos
    )

    # ============================
    # 4) Totales por producto + FEFO
    # ============================

    totales_producto: dict[str, int] = {}   # "producto_key" -> qty total
    lotes_por_producto: dict[str, list] = {}  # "producto" -> [{fv, qty}]

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

        # Lotes por producto (simplificado, sin por-slot aquí)
        if signed_delta > 0:
            fv = mov.fecha_vencimiento
            lotes_por_producto.setdefault(prod_name, []).append(
                {"fv": fv, "qty": signed_delta}
            )
        elif signed_delta < 0:
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
    # 5) Resumen de estados de stock
    # ============================

    resumen_stock = {
        "Crítico": 0,
        "OK": 0,
        "Sobre-stock": 0,
        "Sin configuración": 0,
    }
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}
    estado_producto: dict[str, str] = {}

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
        if est in resumen_stock:
            resumen_stock[est] += 1

    # ============================
    # 6) Resumen de vencimientos
    # ============================

    resumen_venc = {
        "Vencido": 0,
        "<7": 0,
        "<15": 0,
        "<30": 0,
        "<60": 0,
        "Normal": 0,
        "Sin fecha": 0,
    }

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
    # 7) Total unidades en stock + valor inventario
    # ============================

    total_unidades = sum(q for q in totales_producto.values() if q > 0)

    valor_inventario = 0.0
    for prod_key, qty in totales_producto.items():
        if qty <= 0:
            continue
        p = productos_by_name.get(prod_key)
        if p and p.costo_unitario is not None:
            valor_inventario += qty * float(p.costo_unitario)

    valor_inventario = int(round(valor_inventario))

    # ============================
    # 8) Pérdidas por merma últimos 30 días
    # ============================

    desde_30 = hoy - timedelta(days=30)
    desde_30_dt = datetime.combine(desde_30, datetime.min.time())

    salidas_merma = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            Movimiento.tipo == "salida",
            Movimiento.motivo_salida == "merma",
            Movimiento.fecha >= desde_30_dt,
        )
        .all()
    )

    perdidas_merma_30d = 0.0
    for m in salidas_merma:
        p = productos_by_name.get((m.producto or "").lower())
        if p and p.costo_unitario is not None:
            perdidas_merma_30d += abs(m.cantidad or 0) * float(p.costo_unitario)

    perdidas_merma_30d = int(round(perdidas_merma_30d))

    # ============================
    # 9) Últimos movimientos (para tabla)
    # ============================

    movimientos_recientes = (
        db.query(Movimiento)
        .filter(Movimiento.negocio_id == negocio_id)
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(10)
        .all()
    )

    # ============================
    # 10) Gráfico últimos 7 días (siempre 7 días)
    # ============================

    hoy_dt = datetime.utcnow().date()
    labels = []
    entradas_data = []
    salidas_data = []

    for i in range(6, -1, -1):
        dia = hoy_dt - timedelta(days=i)
        labels.append(dia.strftime("%d-%m"))

        e_count = (
            db.query(func.coalesce(func.sum(Movimiento.cantidad), 0))
            .filter(
                Movimiento.negocio_id == negocio_id,
                Movimiento.tipo == "entrada",
                func.date(Movimiento.fecha) == dia,
            )
            .scalar()
        )
        s_count = (
            db.query(func.coalesce(func.sum(Movimiento.cantidad), 0))
            .filter(
                Movimiento.negocio_id == negocio_id,
                Movimiento.tipo == "salida",
                func.date(Movimiento.fecha) == dia,
            )
            .scalar()
        )

        entradas_data.append(int(e_count or 0))
        salidas_data.append(int(s_count or 0))

    chart_data = {
        "labels": labels,
        "entradas": entradas_data,
        "salidas": salidas_data,
    }

    # ============================
    # 11) Alertas pendientes
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
            # Onboarding flags (como los usa el template)
            "faltan_zonas": faltan_zonas,
            "faltan_slots": faltan_slots,
            "faltan_productos": faltan_productos,
            "faltan_movimientos": faltan_movimientos,
            "onboarding_completo": onboarding_completo,
        },
    )
