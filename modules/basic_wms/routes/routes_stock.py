# routes_stock.py
from pathlib import Path
from datetime import date

from fastapi import (
    APIRouter,
    Request,
    Depends,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.models import Movimiento, Producto, Slot, Ubicacion, Zona
from core.security import require_roles_dep
from core.services.services_stock import calcular_estado_stock, estado_css


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER STOCK
# ============================

router = APIRouter(
    prefix="",
    tags=["stock"],
)


# ============================
#           STOCK
# ============================

@router.get("/stock", response_class=HTMLResponse)
async def stock_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Vista de stock consolidado por producto y slot:
    - Calcula stock por slot y por producto (entradas - salidas / ajustes).
    - Evalúa estado por reglas de stock_min / stock_max.
    - Evalúa estado de vencimiento por FEFO basado en movimientos.
    - Aplica filtros por producto, zona, estado y vencimiento.

    Solo accesible para roles: admin y operador.
    """
    hoy = date.today()
    negocio_id = user["negocio_id"]

    # ============================
    # Filtros desde la URL (GET)
    # ============================
    params = request.query_params
    f_producto = (params.get("producto", "") or "").strip()
    f_zona = (params.get("zona", "") or "").strip()
    f_estado = (params.get("estado", "") or "").strip()
    f_vencimiento = (params.get("vencimiento", "") or "").strip()

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

    # ============================
    # 2) Movimientos con join Slot/Ubic/Zona (orden FEFO)
    # ============================
    movimientos = (
        db.query(Movimiento, Slot, Ubicacion, Zona)
        .outerjoin(Slot, Movimiento.zona == Slot.codigo_full)
        .outerjoin(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .outerjoin(Zona, Ubicacion.zona_id == Zona.id)
        .filter(Movimiento.negocio_id == negocio_id)
        .order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        .all()
    )

    totales_producto: dict[str, int] = {}      # prod_key -> qty total
    stock_por_slot: dict[tuple[str, str], dict] = {}
    lotes_por_slot: dict[tuple[str, str], list] = {}

    for mov, slot, ubic, zona in movimientos:
        prod_name = mov.producto
        if not prod_name:
            continue

        prod_key = prod_name.lower()
        zona_str = mov.zona  # código full del slot

        qty = mov.cantidad or 0
        # salidas/ajustes negativos restan, el resto suma
        if mov.tipo == "salida" or (mov.tipo == "ajuste" and qty < 0):
            signed_delta = -abs(qty)
        else:
            signed_delta = abs(qty)

        # Totales por producto (nivel global)
        totales_producto[prod_key] = totales_producto.get(prod_key, 0) + signed_delta

        # Stock por slot
        slot_key = (prod_name, zona_str)
        if slot_key not in stock_por_slot:
            stock_por_slot[slot_key] = {
                "producto": prod_name,
                "zona_str": zona_str,
                "cantidad": 0,
                "slot": slot,
                "ubic": ubic,
                "zona": zona,
            }

        info = stock_por_slot[slot_key]
        info["cantidad"] += signed_delta

        # aseguramos tener las referencias más recientes
        if slot is not None:
            info["slot"] = slot
        if ubic is not None:
            info["ubic"] = ubic
        if zona is not None:
            info["zona"] = zona

        # Lotes por vencimiento (FEFO simplificado)
        lotes = lotes_por_slot.setdefault(slot_key, [])
        if signed_delta > 0:
            fv = mov.fecha_vencimiento
            lotes.append({"fv": fv, "qty": signed_delta})
        elif signed_delta < 0:
            # consumir lotes (FEFO)
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
    # 3) Construir filas base (todas)
    # ============================
    filas_all: list[dict] = []

    for (producto_nombre, zona_str), info in stock_por_slot.items():
        cantidad_slot = info["cantidad"]
        if cantidad_slot == 0:
            continue

        prod_key = producto_nombre.lower()
        prod = productos_by_name.get(prod_key)

        stock_total = totales_producto.get(prod_key, 0)
        stock_min = prod.stock_min if prod else None
        stock_max = prod.stock_max if prod else None

        # Estado por min/max (a nivel producto total, usando service)
        estado_val = calcular_estado_stock(stock_total, stock_min, stock_max)
        estado_css_val = estado_css(estado_val)

        slot = info["slot"]
        ubic = info["ubic"]
        zona_obj = info["zona"]

        slot_codigo = slot.codigo if slot is not None else None

        capacidad = slot.capacidad if slot is not None else None
        ocupacion_pct = None
        if capacidad and capacidad > 0:
            ocupacion_pct = round(cantidad_slot * 100 / capacidad, 1)

        # Estado de vencimiento según lotes restantes
        lotes = lotes_por_slot.get((producto_nombre, zona_str), [])
        fv_min = None
        for l in lotes:
            if l["fv"] is not None:
                if fv_min is None or l["fv"] < fv_min:
                    fv_min = l["fv"]

        venc_estado = "Sin fecha"
        venc_css = "bg-slate-100 text-slate-700 border border-slate-200"
        venc_dias = None

        if fv_min is not None:
            dias_restantes = (fv_min - hoy).days
            venc_dias = dias_restantes

            if dias_restantes < 0:
                venc_estado = "Vencido"
                venc_css = "bg-red-100 text-red-700 border border-red-200"
            elif dias_restantes <= 7:
                venc_estado = "Por vencer <7 días"
                venc_css = "bg-orange-100 text-orange-700 border border-orange-200"
            elif dias_restantes <= 15:
                venc_estado = "Por vencer <15 días"
                venc_css = "bg-amber-100 text-amber-700 border border-amber-200"
            elif dias_restantes <= 30:
                venc_estado = "Por vencer <30 días"
                venc_css = "bg-yellow-100 text-yellow-700 border border-yellow-200"
            elif dias_restantes <= 60:
                venc_estado = "Por vencer <60 días"
                venc_css = "bg-lime-100 text-lime-700 border border-lime-200"
            else:
                venc_estado = "Normal"
                venc_css = "bg-emerald-100 text-emerald-700 border border-emerald-200"

        filas_all.append(
            {
                "producto": producto_nombre,
                "unidad": prod.unidad if prod else "unidad",
                "zona_nombre": zona_obj.nombre if zona_obj is not None else "-",
                "ubicacion_nombre": ubic.nombre if ubic is not None else "-",
                "codigo_full": slot.codigo_full if slot is not None else zona_str,
                "slot_codigo": slot_codigo or "-",
                "cantidad": cantidad_slot,
                "stock_total": stock_total,
                "stock_min": stock_min,
                "stock_max": stock_max,
                "estado": estado_val,
                "estado_css": estado_css_val,
                "capacidad": capacidad,
                "ocupacion_pct": ocupacion_pct,
                "vencimiento_fecha": fv_min,
                "vencimiento_dias": venc_dias,
                "vencimiento_estado": venc_estado,
                "vencimiento_css": venc_css,
            }
        )

    # Productos sin stock pero con reglas configuradas
    for p in productos:
        key = p.nombre.lower()
        if totales_producto.get(key, 0) == 0:
            stock_min = p.stock_min
            stock_max = p.stock_max
            stock_total = 0

            estado_val = calcular_estado_stock(stock_total, stock_min, stock_max)
            estado_css_val = estado_css(estado_val)

            filas_all.append(
                {
                    "producto": p.nombre,
                    "unidad": p.unidad,
                    "zona_nombre": "-",
                    "ubicacion_nombre": "-",
                    "codigo_full": "-",
                    "slot_codigo": "-",
                    "cantidad": 0,
                    "stock_total": stock_total,
                    "stock_min": stock_min,
                    "stock_max": stock_max,
                    "estado": estado_val,
                    "estado_css": estado_css_val,
                    "capacidad": None,
                    "ocupacion_pct": None,
                    "vencimiento_fecha": None,
                    "vencimiento_dias": None,
                    "vencimiento_estado": "Sin fecha",
                    "vencimiento_css": "bg-slate-100 text-slate-700 border border-slate-200",
                }
            )

    # ============================
    # 4) Opciones para selects
    # ============================
    zonas_list = sorted(
        {
            r["zona_nombre"]
            for r in filas_all
            if r["zona_nombre"] and r["zona_nombre"] != "-"
        }
    )
    estados_list = sorted({r["estado"] for r in filas_all})
    venc_list = sorted({r["vencimiento_estado"] for r in filas_all})

    # ============================
    # 5) Aplicar filtros
    # ============================
    filas_filtradas: list[dict] = []

    for r in filas_all:
        if f_producto and f_producto.lower() not in r["producto"].lower():
            continue
        if f_zona and r["zona_nombre"] != f_zona:
            continue
        if f_estado and r["estado"] != f_estado:
            continue
        if f_vencimiento and r["vencimiento_estado"] != f_vencimiento:
            continue
        filas_filtradas.append(r)

    # ============================
    # 6) Ordenar filas filtradas
    # ============================
    filas_filtradas.sort(
        key=lambda r: (
            r["zona_nombre"] or "",
            r["ubicacion_nombre"] or "",
            r["codigo_full"] or "",
            r["producto"].lower(),
        )
    )

    # ============================
    # 7) Resumen de estados (solo filtradas)
    # ============================
    resumen_estados = {
        "Crítico": 0,
        "OK": 0,
        "Sobre-stock": 0,
        "Sin configuración": 0,
    }
    estado_producto: dict[str, str] = {}
    prioridad = {"Crítico": 3, "Sobre-stock": 2, "OK": 1, "Sin configuración": 0}

    for r in filas_filtradas:
        prod = r["producto"]
        est = r["estado"]
        prev = estado_producto.get(prod)
        if prev is None or prioridad.get(est, 0) > prioridad.get(prev, 0):
            estado_producto[prod] = est

    for est in estado_producto.values():
        if est in resumen_estados:
            resumen_estados[est] += 1

    return templates.TemplateResponse(
        "stock.html",
        {
            "request": request,
            "user": user,
            "filas": filas_filtradas,
            "resumen": resumen_estados,
            "zonas_list": zonas_list,
            "estados_list": estados_list,
            "venc_list": venc_list,
            # filtros actuales
            "f_producto": f_producto,
            "f_zona": f_zona,
            "f_estado": f_estado,
            "f_vencimiento": f_vencimiento,
        },
    )
