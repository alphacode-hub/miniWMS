# routes_superadmin.py
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    HTTPException,
    Query,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Negocio, Producto, Movimiento, Alerta, Usuario, Auditoria
from security import require_superadmin_dep, _get_session_payload_from_request, _set_session_cookie_from_payload
from plans import PLANES_CONFIG


# ============================
#   TEMPLATES
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================
#   ROUTER SUPERADMIN
# ============================

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
)


# ============================
# SUPERADMIN
# ============================

@router.get("/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    # Si el superadmin está en modo negocio, lo mandamos al dashboard del negocio
    if user.get("impersonando_negocio_id"):
        return RedirectResponse(url="/dashboard", status_code=302)

    # Totales de negocios
    total_negocios = db.query(Negocio).count()
    negocios_activos = db.query(Negocio).filter(Negocio.estado == "activo").count()
    negocios_suspendidos = db.query(Negocio).filter(Negocio.estado == "suspendido").count()

    # Alertas pendientes (todas)
    alertas_pendientes = db.query(Alerta).filter(Alerta.estado == "pendiente").count()

    return templates.TemplateResponse(
        "superadmin_dashboard.html",
        {
            "request": request,
            "user": user,
            "total_negocios": total_negocios,
            "negocios_activos": negocios_activos,
            "negocios_suspendidos": negocios_suspendidos,
            "alertas_pendientes": alertas_pendientes,
        },
    )


@router.get("/negocios", response_class=HTMLResponse)
async def superadmin_negocios(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocios = db.query(Negocio).all()
    data: list[dict] = []

    for n in negocios:
        usuarios = db.query(Usuario).filter(Usuario.negocio_id == n.id).count()
        productos = db.query(Producto).filter(Producto.negocio_id == n.id).count()

        hace_30 = datetime.utcnow() - timedelta(days=30)
        movimientos = (
            db.query(Movimiento)
            .filter(
                Movimiento.negocio_id == n.id,
                Movimiento.fecha >= hace_30,
            )
            .count()
        )

        data.append(
            {
                "id": n.id,
                "nombre": n.nombre_fantasia,
                "plan": n.plan_tipo,
                "estado": n.estado,
                "usuarios": usuarios,
                "productos": productos,
                "movimientos_30d": movimientos,
                "ultimo_acceso": n.ultimo_acceso,
            }
        )

    return templates.TemplateResponse(
        "superadmin_negocios.html",
        {
            "request": request,
            "user": user,
            "negocios": data,
        },
    )


@router.get("/negocios/{negocio_id}", response_class=HTMLResponse)
async def superadmin_negocio_detalle(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    # 👉 últimos eventos de auditoría de este negocio (para preview opcional)
    eventos = (
        db.query(Auditoria)
        .filter(Auditoria.negocio_id == negocio_id)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(5)
        .all()
    )

    return templates.TemplateResponse(
        "superadmin_negocio_detalle.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "planes": PLANES_CONFIG.keys(),
            "eventos_auditoria": eventos,
        },
    )


@router.post("/negocios/{negocio_id}/update")
async def superadmin_negocio_update(
    request: Request,
    negocio_id: int,
    plan_tipo: str = Form(...),
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    negocio.plan_tipo = plan_tipo
    negocio.estado = estado
    db.commit()

    return RedirectResponse(
        url=f"/superadmin/negocios/{negocio_id}",
        status_code=302,
    )


@router.get("/alertas", response_class=HTMLResponse)
async def superadmin_alertas(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    alertas = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(500)
        .all()
    )

    return templates.TemplateResponse(
        "superadmin_alertas.html",
        {
            "request": request,
            "user": user,
            "alertas": alertas,
        },
    )


@router.get("/negocios/{negocio_id}/ver-como")
async def superadmin_ver_como_negocio(
    negocio_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    """
    Activa 'modo negocio' para el superadmin.
    A partir de aquí, se comporta como admin de ese negocio en rutas de negocio.
    """

    negocio = (
        db.query(Negocio)
        .filter(Negocio.id == negocio_id)
        .first()
    )
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado.")

    payload = _get_session_payload_from_request(request)
    if not payload:
        # sesión inválida -> fuera
        return RedirectResponse("/login", status_code=302)

    # Guardamos datos de modo negocio
    payload["acting_negocio_id"] = negocio.id
    payload["acting_negocio_nombre"] = negocio.nombre_fantasia

    resp = RedirectResponse(url="/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)
    return resp


@router.get("/salir-modo-negocio")
async def superadmin_salir_modo_negocio(
    request: Request,
    user: dict = Depends(require_superadmin_dep),
):
    """
    Desactiva 'modo negocio' y devuelve al dashboard global del superadmin.
    """

    payload = _get_session_payload_from_request(request)
    if not payload:
        return RedirectResponse("/login", status_code=302)

    payload.pop("acting_negocio_id", None)
    payload.pop("acting_negocio_nombre", None)

    resp = RedirectResponse(url="/superadmin/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)
    return resp


@router.get("/negocios/{negocio_id}/auditoria", response_class=HTMLResponse)
async def superadmin_auditoria_negocio(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    """
    Auditoría de un negocio específico vista por el superadmin.
    Permite filtrar por rango de fecha, texto libre y nivel de criticidad.
    """

    negocio = (
        db.query(Negocio)
        .filter(Negocio.id == negocio_id)
        .first()
    )
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    # ============================
    # Filtros desde query params
    # ============================
    params = request.query_params

    texto = (params.get("q") or "").strip()
    fecha_desde_str = (params.get("desde") or "").strip()
    fecha_hasta_str = (params.get("hasta") or "").strip()
    nivel_str = (params.get("nivel") or "").strip()  # 'critico', 'warning', 'info', 'normal', ''

    fecha_desde = None
    fecha_hasta = None

    # Parseo de fechas en formato YYYY-MM-DD
    if fecha_desde_str:
        try:
            fecha_desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d")
        except ValueError:
            fecha_desde = None

    if fecha_hasta_str:
        try:
            fecha_hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d")
            fecha_hasta = fecha_hasta.replace(hour=23, minute=59, second=59)
        except ValueError:
            fecha_hasta = None

    # ============================
    # Query base (sin nivel aún)
    # ============================
    query = (
        db.query(Auditoria)
        .filter(Auditoria.negocio_id == negocio_id)
    )

    if fecha_desde:
        query = query.filter(Auditoria.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.filter(Auditoria.fecha <= fecha_hasta)

    # Búsqueda por texto libre (usuario, acción, detalle)
    if texto:
        like_expr = f"%{texto}%"
        query = query.filter(
            Auditoria.usuario.ilike(like_expr)
            | Auditoria.accion.ilike(like_expr)
            | Auditoria.detalle.ilike(like_expr)
        )

    # Traemos un máximo de 500 desde BD
    registros_db = (
        query
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(500)
        .all()
    )

    # ============================
    # Enriquecemos con nivel y filtramos en memoria
    # ============================
    registros: list[Auditoria] = []
    for r in registros_db:
        nivel = clasificar_evento_auditoria(r.accion, r.detalle)
        # le agregamos un atributo dinámico para usar en el template
        setattr(r, "nivel", nivel)
        registros.append(r)

    # Filtro por nivel, si viene en la URL
    if nivel_str in {"critico", "warning", "info", "normal"}:
        registros = [r for r in registros if getattr(r, "nivel", "normal") == nivel_str]

    return templates.TemplateResponse(
        "superadmin_auditoria.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "registros": registros,
            "filtros": {
                "q": texto,
                "desde": fecha_desde_str,
                "hasta": fecha_hasta_str,
                "nivel": nivel_str,
            },
        },
    )


# ============================
#   HELPERS AUDITORÍA
# ============================

def clasificar_evento_auditoria(accion: str, detalle: str | None = None) -> str:
    """
    Devuelve un nivel de criticidad para un evento de auditoría.

    Niveles posibles:
      - 'critico'
      - 'warning'
      - 'info'
      - 'normal'

    Puedes ajustar los mapeos según los nombres reales de tus acciones.
    """
    a = (accion or "").lower()

    # ⚠️ Críticos: cosas que realmente importan
    if a in {
        "negocio_suspendido",
        "negocio_reactivado",
        "usuario_eliminado",
        "producto_eliminado",
        "stock_borrado_masivo",
        "intento_login_fallido",
    }:
        return "critico"

    # 🟡 Advertencias
    if a in {
        "salida_merma",
        "stock_critico",
        "alerta_creada",
        "producto_modificado",
        "usuario_bloqueado",
    }:
        return "warning"

    # 🔵 Informativos
    if a in {
        "login_ok",
        "logout",
        "producto_creado",
        "entrada_creada",
        "salida_creada",
        "usuario_creado",
    }:
        return "info"

    # Gris / normal
    return "normal"
