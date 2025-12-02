# routes_inventory.py
from pathlib import Path
from datetime import datetime

from fastapi import (
    APIRouter,
    Request,
    Depends,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Movimiento
from security import require_roles_dep
from services.services_audit import registrar_auditoria


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER INVENTARIO / CONTEO
# ============================

router = APIRouter(
    prefix="",
    tags=["inventario"],
)


# ============================
#  HELPERS INVENTARIO
# ============================

def _calcular_resumen_inventario(
    db: Session,
    negocio_id: int,
) -> dict[tuple[str, str], dict]:
    """
    Calcula el stock teórico por (producto_norm, zona) a partir de la tabla de movimientos.
    Devuelve un dict:
      (producto_norm, zona_norm) -> {
          "producto_display": str,
          "zona": str,
          "entradas": int,
          "salidas": int,
      }
    """
    movimientos = (
        db.query(Movimiento)
        .filter(Movimiento.negocio_id == negocio_id)
        .all()
    )

    resumen: dict[tuple[str, str], dict] = {}

    for m in movimientos:
        nombre_original = (m.producto or "").strip()
        if not nombre_original:
            continue

        nombre_norm = nombre_original.lower()
        zona_norm = (m.zona or "").strip()

        key = (nombre_norm, zona_norm)
        if key not in resumen:
            resumen[key] = {
                "producto_display": nombre_original,
                "zona": zona_norm,
                "entradas": 0,
                "salidas": 0,
            }

        if m.tipo == "entrada":
            resumen[key]["entradas"] += m.cantidad or 0
        elif m.tipo == "salida":
            resumen[key]["salidas"] += m.cantidad or 0

    return resumen


# ============================
#      INVENTARIO / CONTEO
# ============================

@router.get("/inventario", response_class=HTMLResponse)
async def inventario_form(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Muestra el inventario teórico por producto y zona
    para realizar conteos físicos y generar ajustes.

    Solo accesible para roles: admin y operador.
    """
    negocio_id = user["negocio_id"]

    # 1) Calcular stock teórico por (producto_norm, zona)
    resumen = _calcular_resumen_inventario(db, negocio_id)

    # 2) Construir lista para la tabla
    stock_items: list[dict] = []
    for (_prod_norm, _zona_norm), data in resumen.items():
        stock_actual = (data["entradas"] or 0) - (data["salidas"] or 0)
        stock_items.append(
            {
                "producto": data["producto_display"],
                "zona": data["zona"],
                "stock_actual": stock_actual,
            }
        )

    # Ordenamos por zona y nombre de producto
    stock_items.sort(key=lambda x: (x["zona"], x["producto"]))

    return templates.TemplateResponse(
        "inventario.html",
        {
            "request": request,
            "user": user,
            "stock_items": stock_items,
        },
    )


@router.post("/inventario", response_class=HTMLResponse)
async def inventario_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Procesa el formulario de conteo de inventario:
    - Recalcula stock teórico.
    - Compara contra el conteo físico.
    - Genera movimientos de ajuste (entrada/salida) con motivo 'ajuste_inventario'.

    Solo accesible para roles: admin y operador.
    """
    negocio_id = user["negocio_id"]

    form = await request.form()
    try:
        total_items = int(form.get("total_items", 0))
    except ValueError:
        total_items = 0

    # 1) Recalcular stock teórico igual que en el GET
    resumen = _calcular_resumen_inventario(db, negocio_id)

    # 2) Procesar conteos y generar ajustes
    ajustes_realizados = 0

    for i in range(total_items):
        producto = (form.get(f"producto_{i}") or "").strip()
        zona = (form.get(f"zona_{i}") or "").strip()
        conteo_str = form.get(f"conteo_{i}") or ""

        if not producto:
            continue

        try:
            conteo = int(conteo_str)
        except ValueError:
            conteo = 0

        key_norm = (producto.lower(), zona)
        data = resumen.get(key_norm)

        stock_teorico = 0
        if data is not None:
            stock_teorico = (data["entradas"] or 0) - (data["salidas"] or 0)

        diff = conteo - stock_teorico
        if diff == 0:
            continue  # no hay ajuste

        # Si diff > 0 → faltaba stock en el sistema → registramos una "entrada".
        # Si diff < 0 → sobraba stock en el sistema → registramos una "salida".
        tipo_mov = "entrada" if diff > 0 else "salida"
        cantidad_ajuste = abs(diff)

        movimiento = Movimiento(
            negocio_id=negocio_id,
            usuario=user["email"],
            tipo=tipo_mov,
            producto=producto,
            cantidad=cantidad_ajuste,
            zona=zona,
            fecha=datetime.utcnow(),
            # usamos este campo para marcar claramente que es ajuste
            motivo_salida="ajuste_inventario",
        )
        db.add(movimiento)
        ajustes_realizados += 1

        print(
            f">>> AJUSTE INVENTARIO: {tipo_mov} {cantidad_ajuste} x '{producto}' en {zona} "
            f"(teórico={stock_teorico}, conteo={conteo})"
        )

        # Auditoría del ajuste
        registrar_auditoria(
            db,
            user,
            accion="ajuste_inventario",
            detalle={
                "producto": producto,
                "zona": zona,
                "tipo_mov": tipo_mov,
                "cantidad_ajuste": cantidad_ajuste,
                "stock_teorico": stock_teorico,
                "conteo": conteo,
                "motivo": "ajuste_inventario",
            },
        )

    if ajustes_realizados > 0:
        db.commit()

    # Luego de ajustar, volvemos al /stock para ver el resultado
    return RedirectResponse(url="/stock", status_code=302)
