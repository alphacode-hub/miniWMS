# modules/inbound_orbion/routes/routes_inbound_analytics.py
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from core.database import get_db

from modules.inbound_orbion.services.services_inbound_core import InboundDomainError
from modules.inbound_orbion.services.services_inbound_logging import (
    log_inbound_event,
    log_inbound_error,
)
from modules.inbound_orbion.services.services_inbound_proveedores import listar_proveedores
from modules.inbound_orbion.services.services_inbound_analytics import (
    _dt_from_iso,  # helper interno OK
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


def _norm_proveedor_id(v: int | None) -> int | None:
    # UI a veces manda 0 o vacío
    if v in (None, 0):
        return None
    return int(v)


def _build_analytics_url(*, desde: str | None, hasta: str | None, proveedor_id: int | None) -> str:
    params: list[str] = []
    if (desde or "").strip():
        params.append(f"desde={_qp(desde)}")
    if (hasta or "").strip():
        params.append(f"hasta={_qp(hasta)}")
    if proveedor_id:
        params.append(f"proveedor_id={int(proveedor_id)}")

    if not params:
        return "/inbound/analytics"
    return "/inbound/analytics?" + "&".join(params)


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

    proveedor_id = _norm_proveedor_id(proveedor_id)
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
                "filtros": {
                    "desde": desde or "",
                    "hasta": hasta or "",
                    "proveedor_id": proveedor_id or "",
                },
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
                "filtros": {
                    "desde": desde or "",
                    "hasta": hasta or "",
                    "proveedor_id": proveedor_id or "",
                },
                "snapshots": [],
                "ok": None,
                "error": str(e),
                "modulo_nombre": "Orbion Inbound",
            },
            status_code=200,
        )
    except Exception as e:
        log_inbound_error(
            "analytics_unhandled_error",
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
                "filtros": {
                    "desde": desde or "",
                    "hasta": hasta or "",
                    "proveedor_id": proveedor_id or "",
                },
                "snapshots": [],
                "ok": None,
                "error": "Error inesperado en analytics. Revisa logs.",
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

    proveedor_id = _norm_proveedor_id(proveedor_id)
    dt_desde = _dt_from_iso(desde)
    dt_hasta = _dt_from_iso(hasta)

    try:
        payload = obtener_analytics_inbound(
            db,
            negocio_id=negocio_id,
            desde=dt_desde,
            hasta=dt_hasta,
            proveedor_id=proveedor_id,
        )
        csv_text = exportar_analytics_csv(payload)

        # Nombre friendly para operación (Chile):
        # orbion_inbound_analytics_2025-12-01_2025-12-31_prov-12.csv
        d1 = (desde or "").strip() or "all"
        d2 = (hasta or "").strip() or "all"
        ptag = f"_prov-{proveedor_id}" if proveedor_id else ""
        filename = f"orbion_inbound_analytics_{d1}_{d2}{ptag}.csv"

        log_inbound_event(
            "analytics_export_csv",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            filtros={"desde": desde, "hasta": hasta, "proveedor_id": proveedor_id},
        )

        # Excel-friendly (tildes/ñ): utf-8-sig agrega BOM
        content = csv_text.encode("utf-8-sig")

        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        log_inbound_error(
            "analytics_export_csv_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return _redirect(_build_analytics_url(desde=desde, hasta=hasta, proveedor_id=proveedor_id),
                         error="No se pudo exportar CSV. Revisa logs.")


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

    proveedor_id = _norm_proveedor_id(proveedor_id)
    back_url = _build_analytics_url(desde=desde, hasta=hasta, proveedor_id=proveedor_id)

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

        log_inbound_event(
            "analytics_snapshot_created",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            snapshot_id=snap.id,
            filtros={"desde": desde, "hasta": hasta, "proveedor_id": proveedor_id},
        )

        return _redirect(back_url, ok=f"Snapshot creado (ID #{snap.id}).")

    except Exception as e:
        db.rollback()
        log_inbound_error(
            "analytics_snapshot_create_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            error=str(e),
        )
        return _redirect(back_url, error="No se pudo crear snapshot. Revisa logs.")


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

        meta = payload.get("meta", {}) or {}
        dt_desde = _dt_from_iso(meta.get("desde"))
        dt_hasta = _dt_from_iso(meta.get("hasta"))

        scoring = calcular_scoring_proveedores(
            db,
            negocio_id=negocio_id,
            desde=dt_desde,
            hasta=dt_hasta,
        )

        log_inbound_event(
            "analytics_snapshot_view",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            snapshot_id=snapshot_id,
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
        log_inbound_error(
            "analytics_snapshot_domain_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            snapshot_id=snapshot_id,
            error=str(e),
        )
        return _redirect("/inbound/analytics", error=str(e))
    except Exception as e:
        log_inbound_error(
            "analytics_snapshot_unhandled_error",
            negocio_id=negocio_id,
            user_email=user.get("email"),
            snapshot_id=snapshot_id,
            error=str(e),
        )
        return _redirect("/inbound/analytics", error="No se pudo abrir snapshot. Revisa logs.")
