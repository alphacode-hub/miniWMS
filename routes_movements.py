# routes_movements.py
from pathlib import Path
from datetime import datetime

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Movimiento, Producto, Slot, Ubicacion, Zona
from security import require_roles_dep
from services_slots import get_slots_negocio
from services_audit import registrar_auditoria
from services_alerts import evaluar_alertas_stock, evaluar_alertas_vencimiento



# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER MOVIMIENTOS
# ============================

router = APIRouter(
    prefix="",
    tags=["movimientos"],
)


# ============================
#   LISTADO DE MOVIMIENTOS
# ============================

@router.get("/movimientos", response_class=HTMLResponse)
async def movimientos_view(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Listado de movimientos con filtros básicos:
    - rango de fechas
    - tipo de movimiento
    - producto (contiene)
    - usuario (contiene)

    Solo accesible para roles: admin y operador.
    """
    params = request.query_params

    fecha_desde_str = params.get("desde", "")
    fecha_hasta_str = params.get("hasta", "")
    tipo_filtro = params.get("tipo", "")
    producto_filtro = params.get("producto", "")
    usuario_filtro = params.get("usuario", "")

    negocio_id = user["negocio_id"]

    query = db.query(Movimiento).filter(Movimiento.negocio_id == negocio_id)

    # Filtro por fecha desde
    if fecha_desde_str:
        try:
            dt_desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d")
            query = query.filter(Movimiento.fecha >= dt_desde)
        except ValueError:
            pass

    # Filtro por fecha hasta (inclusive día completo)
    if fecha_hasta_str:
        try:
            dt_hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d")
            dt_hasta_fin = dt_hasta.replace(hour=23, minute=59, second=59)
            query = query.filter(Movimiento.fecha <= dt_hasta_fin)
        except ValueError:
            pass

    # Filtro por tipo
    if tipo_filtro:
        query = query.filter(Movimiento.tipo == tipo_filtro)

    # Filtro por producto (contiene, case-insensitive)
    if producto_filtro:
        query = query.filter(
            func.lower(Movimiento.producto).like(f"%{producto_filtro.lower()}%")
        )

    # Filtro por usuario (contiene, case-insensitive)
    if usuario_filtro:
        query = query.filter(
            func.lower(Movimiento.usuario).like(f"%{usuario_filtro.lower()}%")
        )

    # Orden más reciente primero + límite de seguridad
    movimientos = (
        query.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(500)
        .all()
    )

    # ==========================
    # KPIs sobre los movimientos
    # ==========================
    total_movimientos = len(movimientos)

    def es_ajuste(m: Movimiento) -> bool:
        """
        Consideramos como 'ajuste' cualquier movimiento cuyo motivo_salida
        sea 'ajuste_inventario' (los generados desde /inventario).
        """
        return (m.motivo_salida or "").strip().lower() == "ajuste_inventario"

    total_ajustes = sum(1 for m in movimientos if es_ajuste(m))

    total_entradas = sum(
        1 for m in movimientos
        if m.tipo == "entrada" and not es_ajuste(m)
    )
    total_salidas = sum(
        1 for m in movimientos
        if m.tipo == "salida" and not es_ajuste(m)
    )

    # Combos de filtro
    productos_distintos = (
        db.query(Movimiento.producto)
        .filter(Movimiento.negocio_id == negocio_id)
        .distinct()
        .order_by(Movimiento.producto.asc())
        .all()
    )
    usuarios_distintos = (
        db.query(Movimiento.usuario)
        .filter(Movimiento.negocio_id == negocio_id)
        .distinct()
        .order_by(Movimiento.usuario.asc())
        .all()
    )
    tipos_distintos = (
        db.query(Movimiento.tipo)
        .filter(Movimiento.negocio_id == negocio_id)
        .distinct()
        .order_by(Movimiento.tipo.asc())
        .all()
    )

    productos_list = [r[0] for r in productos_distintos if r[0]]
    usuarios_list = [r[0] for r in usuarios_distintos if r[0]]
    tipos_list = [r[0] for r in tipos_distintos if r[0]]

    return templates.TemplateResponse(
        "movimientos.html",
        {
            "request": request,
            "user": user,
            "movimientos": movimientos,
            "productos_list": productos_list,
            "usuarios_list": usuarios_list,
            "tipos_list": tipos_list,
            "f_desde": fecha_desde_str,
            "f_hasta": fecha_hasta_str,
            "f_tipo": tipo_filtro,
            "f_producto": producto_filtro,
            "f_usuario": usuario_filtro,
            "total_movimientos": total_movimientos,
            "total_entradas": total_entradas,
            "total_salidas": total_salidas,
            "total_ajustes": total_ajustes,
        },
    )


# ============================
#     MOVIMIENTO DE SALIDA
# ============================

@router.get("/movimientos/salida", response_class=HTMLResponse)
async def salida_form(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Formulario para registrar una salida de mercadería.
    Solo roles: admin y operador.
    """
    negocio_id = user["negocio_id"]

    # Productos activos del negocio
    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        # Sin productos → ir a crear productos
        return RedirectResponse("/productos/nuevo", status_code=302)

    # Slots disponibles del negocio
    slots = get_slots_negocio(db, negocio_id)
    if not slots:
        # Sin slots → ir a configurar zonas/ubicaciones/slots
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "salida.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_id": "",
        },
    )


@router.post("/movimientos/salida", response_class=HTMLResponse)
async def salida_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    motivo_salida: str = Form(""),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Procesa el formulario de salida:
    - valida stock disponible en la zona/slot
    - registra el movimiento
    - dispara auditoría y evaluación de alertas
    """
    negocio_id = user["negocio_id"]

    producto = (producto or "").strip()

    # Buscar slot + validar que pertenezca al negocio
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not slot:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)
        return templates.TemplateResponse(
            "salida.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "La ubicación seleccionada no es válida.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
            },
            status_code=400,
        )

    zona_str = slot.codigo_full

    # 1) Calcular stock actual de ese producto + zona + negocio
    movimientos = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            func.lower(Movimiento.producto) == producto.lower(),
            Movimiento.zona == zona_str,
        )
        .all()
    )

    entradas = sum((m.cantidad or 0) for m in movimientos if m.tipo == "entrada")
    salidas = sum((m.cantidad or 0) for m in movimientos if m.tipo == "salida")
    stock_actual = entradas - salidas

    # 2) Validar stock suficiente
    if cantidad > stock_actual:
        error_msg = (
            f"No puedes registrar una salida de {cantidad} unidad(es) de '{producto}' "
            f"en {zona_str} porque el stock actual es {stock_actual}."
        )
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)
        return templates.TemplateResponse(
            "salida.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": error_msg,
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
            },
            status_code=400,
        )

    # 3) Registrar salida
    movimiento = Movimiento(
        negocio_id=negocio_id,
        usuario=user["email"],
        tipo="salida",
        producto=producto,
        cantidad=cantidad,
        zona=zona_str,
        fecha=datetime.utcnow(),
        motivo_salida=(motivo_salida or None),
    )

    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    # Auditoría
    registrar_auditoria(
        db,
        user,
        accion="salida_creada",
        detalle={
            "movimiento_id": movimiento.id,
            "producto": producto,
            "cantidad": cantidad,
            "zona": zona_str,
            "motivo_salida": motivo_salida or None,
            "comentario": (comentario or "").strip() or None,
        },
    )

    # Evaluar alertas (stub por ahora)
    evaluar_alertas_stock(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="salida",
    )

    print(">>> NUEVA SALIDA:", movimiento.id, producto, cantidad, "en", zona_str)

    return RedirectResponse(url="/dashboard", status_code=302)

# ============================
#     MOVIMIENTO DE ENTRADA
# ============================

@router.get("/movimientos/entrada", response_class=HTMLResponse)
async def entrada_form(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Formulario para registrar una entrada de mercadería.
    Solo accesible para roles: admin y operador.
    """
    negocio_id = user["negocio_id"]

    # Productos activos del negocio
    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        # Sin productos → forzar flujo a creación de producto
        return RedirectResponse("/productos/nuevo", status_code=302)

    # Slots configurados del negocio
    slots = get_slots_negocio(db, negocio_id)
    if not slots:
        # Sin slots → ir a configurar el diseño del almacén
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "entrada.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_id": "",
            "fecha_vencimiento": "",
        },
    )


@router.post("/movimientos/entrada", response_class=HTMLResponse)
async def entrada_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_id: int = Form(...),
    fecha_vencimiento: str = Form(""),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Procesa el formulario de entrada:
    - valida slot
    - registra movimiento
    - dispara auditoría y reglas de alertas (stock + vencimiento)
    """
    negocio_id = user["negocio_id"]

    producto = (producto or "").strip()

    # Buscar slot con su ubicación y zona, validando que pertenezca al negocio
    slot = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    if not slot:
        # Slot inválido → volver al formulario con mensaje
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)
        return templates.TemplateResponse(
            "entrada.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "La ubicación seleccionada no es válida.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_id": slot_id,
                "fecha_vencimiento": fecha_vencimiento,
            },
            status_code=400,
        )

    zona_str = slot.codigo_full

    # Parsear fecha de vencimiento (si viene)
    fv_date = None
    fv_str = (fecha_vencimiento or "").strip()
    if fv_str:
        try:
            fv_date = datetime.strptime(fv_str, "%Y-%m-%d").date()
        except ValueError:
            # Si viene mal, la ignoramos en este MVP
            fv_date = None

    # Crear movimiento de entrada
    movimiento = Movimiento(
        negocio_id=negocio_id,
        usuario=user["email"],
        tipo="entrada",
        producto=producto,
        cantidad=cantidad,
        zona=zona_str,
        fecha=datetime.utcnow(),
        fecha_vencimiento=fv_date,
    )

    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)

    # Auditoría
    registrar_auditoria(
        db,
        user,
        accion="entrada_creada",
        detalle={
            "movimiento_id": movimiento.id,
            "producto": producto,
            "cantidad": cantidad,
            "zona": zona_str,
            "fecha_vencimiento": str(fv_date) if fv_date else None,
        },
    )

    # Evaluar alertas de stock tras la entrada
    evaluar_alertas_stock(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="entrada",
    )

    # Evaluar alertas de vencimiento (FEFO simplificado / futuro)
    evaluar_alertas_vencimiento(
        db=db,
        user=user,
        producto_nombre=producto,
        origen="entrada",
    )

    print(
        ">>> NUEVA ENTRADA:",
        movimiento.id,
        movimiento.producto,
        movimiento.cantidad,
        "en",
        zona_str,
        "vence:",
        movimiento.fecha_vencimiento,
    )

    return RedirectResponse(url="/dashboard", status_code=302)

# ============================
#     MOVIMIENTO DE TRANSFERENCIA
# ============================

@router.get("/transferencia", response_class=HTMLResponse)
async def transferencia_form(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Formulario para transferir stock entre slots dentro del mismo negocio.
    Solo accesible para roles: admin y operador.
    """
    negocio_id = user["negocio_id"]

    # Productos activos del negocio
    productos = (
        db.query(Producto)
        .filter(
            Producto.negocio_id == negocio_id,
            Producto.activo == 1,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    if not productos:
        return RedirectResponse("/productos/nuevo", status_code=302)

    # Slots configurados del negocio
    slots = get_slots_negocio(db, negocio_id)
    if not slots:
        return RedirectResponse("/zonas", status_code=302)

    return templates.TemplateResponse(
        "transferencia.html",
        {
            "request": request,
            "user": user,
            "productos": productos,
            "slots": slots,
            "error": None,
            "producto": "",
            "cantidad": "",
            "slot_origen_id": "",
            "slot_destino_id": "",
        },
    )


@router.post("/transferencia", response_class=HTMLResponse)
async def transferencia_submit(
    request: Request,
    producto: str = Form(...),
    cantidad: int = Form(...),
    slot_origen_id: int = Form(...),
    slot_destino_id: int = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador")),
):
    """
    Procesa la transferencia:
    - valida que origen ≠ destino
    - valida slots pertenecen al negocio
    - verifica stock suficiente en el slot origen
    - registra salida en origen y entrada en destino
    """
    negocio_id = user["negocio_id"]
    producto = (producto or "").strip()

    # 1) Origen y destino no pueden ser el mismo
    if slot_origen_id == slot_destino_id:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)
        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "El slot de origen y el de destino no pueden ser el mismo.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    # 2) Buscar slots de origen y destino, validando que sean del negocio
    slot_origen = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_origen_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )
    slot_destino = (
        db.query(Slot)
        .join(Ubicacion, Slot.ubicacion_id == Ubicacion.id)
        .join(Zona, Ubicacion.zona_id == Zona.id)
        .filter(
            Slot.id == slot_destino_id,
            Zona.negocio_id == negocio_id,
        )
        .first()
    )

    if not slot_origen or not slot_destino:
        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)
        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": "Alguno de los slots seleccionados no es válido.",
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    zona_origen_str = slot_origen.codigo_full
    zona_destino_str = slot_destino.codigo_full

    # 3) Calcular stock actual en el slot de origen para ese producto
    movimientos_origen = (
        db.query(Movimiento)
        .filter(
            Movimiento.negocio_id == negocio_id,
            func.lower(Movimiento.producto) == producto.lower(),
            Movimiento.zona == zona_origen_str,
        )
        .all()
    )

    entradas = sum((m.cantidad or 0) for m in movimientos_origen if m.tipo == "entrada")
    salidas = sum((m.cantidad or 0) for m in movimientos_origen if m.tipo == "salida")
    stock_origen = entradas - salidas

    if cantidad > stock_origen:
        error_msg = (
            f"No puedes transferir {cantidad} unidad(es) de '{producto}' "
            f"desde {zona_origen_str} porque el stock actual es {stock_origen}."
        )

        productos = (
            db.query(Producto)
            .filter(
                Producto.negocio_id == negocio_id,
                Producto.activo == 1,
            )
            .order_by(Producto.nombre.asc())
            .all()
        )
        slots = get_slots_negocio(db, negocio_id)

        return templates.TemplateResponse(
            "transferencia.html",
            {
                "request": request,
                "user": user,
                "productos": productos,
                "slots": slots,
                "error": error_msg,
                "producto": producto,
                "cantidad": cantidad,
                "slot_origen_id": slot_origen_id,
                "slot_destino_id": slot_destino_id,
            },
            status_code=400,
        )

    # 4) Crear salida en origen
    mov_salida = Movimiento(
        negocio_id=negocio_id,
        usuario=user["email"],
        tipo="salida",
        producto=producto,
        cantidad=cantidad,
        zona=zona_origen_str,
        fecha=datetime.utcnow(),
    )

    # 5) Crear entrada en destino
    mov_entrada = Movimiento(
        negocio_id=negocio_id,
        usuario=user["email"],
        tipo="entrada",
        producto=producto,
        cantidad=cantidad,
        zona=zona_destino_str,
        fecha=datetime.utcnow(),
    )

    db.add(mov_salida)
    db.add(mov_entrada)
    db.commit()
    db.refresh(mov_salida)
    db.refresh(mov_entrada)

    # Auditoría de la transferencia
    registrar_auditoria(
        db,
        user,
        accion="transferencia_creada",
        detalle={
            "producto": producto,
            "cantidad": cantidad,
            "zona_origen": zona_origen_str,
            "zona_destino": zona_destino_str,
            "mov_salida_id": mov_salida.id,
            "mov_entrada_id": mov_entrada.id,
        },
    )

    print(
        f">>> TRANSFERENCIA: {cantidad} x '{producto}' "
        f"de {zona_origen_str} a {zona_destino_str} "
        f"(mov_salida={mov_salida.id}, mov_entrada={mov_entrada.id})"
    )

    # Te llevo a stock para ver el efecto
    return RedirectResponse(url="/stock", status_code=302)

# ============================
#        HISTORIAL
# ============================

@router.get("/movimientos/historial", response_class=HTMLResponse)
async def movimientos_historial(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles_dep("admin", "operador", "superadmin")),
):
    """
    Historial reciente de movimientos.
    - admin/operador → historial del negocio asignado
    - superadmin     → historial global
    """
    rol = user["rol"]

    if rol == "superadmin":
        # Superadmin ve todos los movimientos
        movimientos = (
            db.query(Movimiento)
            .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
            .limit(200)
            .all()
        )
    else:
        # admin / operador → solo movimientos de su negocio
        negocio_id = user["negocio_id"]
        movimientos = (
            db.query(Movimiento)
            .filter(Movimiento.negocio_id == negocio_id)
            .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
            .limit(50)
            .all()
        )

    return templates.TemplateResponse(
        "historial.html",
        {
            "request": request,
            "user": user,
            "movimientos": movimientos,
        },
    )



