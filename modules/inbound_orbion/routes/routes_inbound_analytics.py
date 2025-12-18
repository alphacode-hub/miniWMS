from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_logging import log_inbound_event, log_inbound_error
from modules.inbound_orbion.services.services_inbound_proveedores import listar_proveedores
from modules.inbound_orbion.services.services_inbound_analytics import (
    _dt_from_iso,  # ok usar helper interno, o cópialo acá si prefieres
    obtener_analytics_inbound,
    exportar_analytics_csv,
    crear_snapshot_analytics,
    listar_snapshots_analytics,
    obtener_snapshot_analytics,
    calcular_scoring_proveedores,
)

from .inbound_common import templates, inbound_roles_dep, get_negocio_or_404

router = APIRouter()


# =========================================================
# Helpers UX
# =========================================================

def _qp(v: str | None) -> str:
    return quote_plus((v or "").strip())


def _redirect(url: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    if ok:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ok={_qp(ok)}"
    if error:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}error={_qp(error)}"
    return RedirectResponse(url=url, status_code=302)


def _negocio_id(user: dict) -> int:
    nid = user.get("negocio_id")
    if not nid:
        raise InboundDomainError("No se encontró negocio_id en sesión.")
    return int(nid)


# =========================================================
# v1 Dashboard Analytics
# =========================================================

@router.get("/analytics", response_class=HTMLResponse)
async def inbound_analytics_view(
    request: Request,
    desde: str | None = None,
    hasta: str | None = None,
    proveedor_id: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id(user)
    get_negocio_or_404(db, negocio_id)

    dt_desde = _dt_from_iso(desde)
    dt_hasta = _dt_from_iso(hasta)

    try:
        proveedores = listar_proveedores(db, negocio_id=negocio_id, solo_activos=True)

        payload = obtener_analytics_inbound(
            db,
            negocio_id=negocio_id,
            desde=dt_desde,
            hasta=dt_hasta,
            proveedor_id=proveedor_id,
        )

        scoring = calcular_scoring_proveedores(
            db,
            negocio_id=negocio_id,
            desde=dt_desde,
            hasta=dt_hasta,
        )

        log_inbound_event(
            "analytics_view",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            filtros={"desde": desde, "hasta": hasta, "proveedor_id": proveedor_id},
        )

        return templates.TemplateResponse(
            "inbound_analytics.html",
            {
                "request": request,
                "user": user,
                "proveedores": proveedores,
                "analytics": payload,
                "scoring": scoring,
                "filtros": {"desde": desde or "", "hasta": hasta or "", "proveedor_id": proveedor_id or ""},
                "snapshots": listar_snapshots_analytics(db, negocio_id=negocio_id, limit=15),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        log_inbound_error(
            "analytics_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return templates.TemplateResponse(
            "inbound_analytics.html",
            {
                "request": request,
                "user": user,
                "proveedores": [],
                "analytics": None,
                "scoring": [],
                "filtros": {"desde": desde or "", "hasta": hasta or "", "proveedor_id": proveedor_id or ""},
                "snapshots": [],
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=200,
        )


# =========================================================
# v1.1 Export CSV
# =========================================================

@router.get("/analytics/export.csv")
async def inbound_analytics_export_csv(
    request: Request,
    desde: str | None = None,
    hasta: str | None = None,
    proveedor_id: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id(user)
    get_negocio_or_404(db, negocio_id)

    dt_desde = _dt_from_iso(desde)
    dt_hasta = _dt_from_iso(hasta)

    payload = obtener_analytics_inbound(
        db,
        negocio_id=negocio_id,
        desde=dt_desde,
        hasta=dt_hasta,
        proveedor_id=proveedor_id,
    )
    csv_text = exportar_analytics_csv(payload)

    filename = "orbion_inbound_analytics.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =========================================================
# v2 Snapshots
# =========================================================

@router.post("/analytics/snapshots/crear", response_class=HTMLResponse)
async def inbound_analytics_snapshot_crear(
    request: Request,
    desde: str | None = None,
    hasta: str | None = None,
    proveedor_id: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id(user)
    get_negocio_or_404(db, negocio_id)

    try:
        payload = obtener_analytics_inbound(
            db,
            negocio_id=negocio_id,
            desde=_dt_from_iso(desde),
            hasta=_dt_from_iso(hasta),
            proveedor_id=proveedor_id,
        )
        snap = crear_snapshot_analytics(db, negocio_id=negocio_id, payload=payload)
        db.commit()

        return _redirect("/inbound/analytics", ok=f"Snapshot creado (ID #{snap.id}).")

    except Exception as e:
        db.rollback()
        log_inbound_error(
            "analytics_snapshot_create_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return _redirect("/inbound/analytics", error="No se pudo crear snapshot. Revisa logs.")


@router.get("/analytics/snapshots/{snapshot_id}", response_class=HTMLResponse)
async def inbound_analytics_snapshot_ver(
    request: Request,
    snapshot_id: int,
    db: Session = Depends(get_db),
    user=Depends(inbound_roles_dep()),
):
    negocio_id = _negocio_id(user)
    get_negocio_or_404(db, negocio_id)

    try:
        proveedores = listar_proveedores(db, negocio_id=negocio_id, solo_activos=True)
        payload = obtener_snapshot_analytics(db, negocio_id=negocio_id, snapshot_id=snapshot_id)

        # scoring re-calculado para rango del snapshot (si existe)
        meta = payload.get("meta", {}) or {}
        dt_desde = meta.get("desde")
        dt_hasta = meta.get("hasta")
        scoring = calcular_scoring_proveedores(
            db,
            negocio_id=negocio_id,
            desde=datetime.fromisoformat(dt_desde) if dt_desde else None,
            hasta=datetime.fromisoformat(dt_hasta) if dt_hasta else None,
        )

        return templates.TemplateResponse(
            "inbound_analytics.html",
            {
                "request": request,
                "user": user,
                "proveedores": proveedores,
                "analytics": payload,
                "scoring": scoring,
                "filtros": {"desde": "", "hasta": "", "proveedor_id": ""},
                "snapshots": listar_snapshots_analytics(db, negocio_id=negocio_id, limit=15),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
                "modulo_nombre": "Orbion Inbound",
            },
        )

    except InboundDomainError as e:
        return _redirect("/inbound/analytics", error=str(e))
