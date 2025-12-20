# core/routes/routes_health.py
"""
Health & Observability routes – ORBION (SaaS enterprise)

✔ Endpoints SOLO para superadmin
✔ Health básico (rápido y seguro)
✔ Smoke test de dominio
✔ Respuestas JSON consistentes
✔ HTML enterprise para humanos (browser)
✔ Logs estructurados
✔ Sin exponer datos sensibles

Regla de oro:
- JSON (format=json) mantiene contrato original (run_smoke_test tal cual)
- HTML usa un "view model" normalizado para render simple
- UI muestra timestamps en CL (America/Santiago) sin alterar UTC en JSON
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.logging_config import logger
from core.models.time import utcnow
from core.security import require_roles_dep
from core.web import templates
from core.services.services_observability import (
    check_core_entities,
    check_db_connection,
    run_smoke_test,
)
from core.formatting import cl_datetime, cl_num  # ✅ Chile formatting helpers


# ============================
# ROUTER
# ============================

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
)


def _wants_json(request: Request) -> bool:
    fmt = (request.query_params.get("format") or "").lower().strip()
    if fmt == "json":
        return True
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept and "text/html" not in accept


# ============================
# NORMALIZACIÓN (solo para HTML)
# ============================

def _normalize_smoke_checks_for_view(checks: list[Any]) -> list[dict[str, Any]]:
    """
    Convierte checks del contrato actual:
      {"name": "...", "ok": bool, "info": any}
    a un formato amigable para UI:
      {"name": "...", "status": "ok|fail|unknown", "detail": str, "info": any}

    Importante:
    - Esto NO cambia el JSON público si se pide format=json.
    """
    out: list[dict[str, Any]] = []

    for c in (checks or []):
        if not isinstance(c, dict):
            out.append({"name": str(c), "status": "unknown", "detail": "", "info": None})
            continue

        name = c.get("name") or c.get("check") or c.get("key") or "check"

        status = c.get("status")
        if status is None:
            if c.get("ok") is True:
                status = "ok"
            elif c.get("ok") is False:
                status = "fail"
            else:
                status = "unknown"

        info = c.get("info")
        detail = c.get("detail") or c.get("message") or ""

        if not detail and isinstance(info, dict):
            if "negocio_id" in info:
                detail = f"negocio_id={info.get('negocio_id')}"
            elif "total" in info:
                detail = f"total={info.get('total')}"
            elif "total_negocios" in info:
                detail = f"negocios={info.get('total_negocios')}"
            elif "modules" in info and isinstance(info.get("modules"), list):
                mods = info.get("modules") or []
                detail = f"modules={','.join(mods[:4])}" if mods else "modules=none"
            elif "status" in info:
                detail = f"status={info.get('status')}"
            else:
                detail = "ok" if status == "ok" else ""

        elif not detail and info is not None and not isinstance(info, dict):
            detail = str(info)

        out.append({"name": name, "status": status, "detail": detail, "info": info})

    return out


def _build_health_view(payload: dict[str, Any]) -> dict[str, Any]:
    """
    View model SOLO para HTML (Chile formatting).
    No tocar payload JSON.
    """
    ts_utc = payload.get("timestamp_utc")  # str iso
    elapsed_ms = payload.get("elapsed_ms")

    # timestamp_cl: formatea "ahora" (no parseamos iso para evitar edge cases)
    # Mejor: si quieres cl exacto del timestamp_utc, pásame el dt, pero esto es suficiente
    # y consistente con tu ruta (ts se calcula en el momento).
    # En esta ruta, payload.timestamp_utc viene de un dt "ts" que ya calculamos.
    # Entonces acá simplemente calculamos cl desde utcnow() NO: lo correcto es calcularlo arriba.
    # Por eso, esta función se invoca con "ts_dt" calculado arriba mediante args extras.
    return {
        "timestamp_utc": ts_utc,
        "timestamp_cl": payload.get("timestamp_cl"),  # lo seteamos arriba SOLO para HTML
        "elapsed_ms": elapsed_ms,
        "elapsed_ms_cl": cl_num(elapsed_ms, 2) if elapsed_ms is not None else "-",
        "status": payload.get("status"),
        "db": payload.get("db"),
        "core_entities": payload.get("core_entities"),
        "error": payload.get("error"),
    }


# ============================
# HEALTH BÁSICO
# ============================

@router.get("/health", response_class=HTMLResponse)
async def health_root(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),
):
    start = utcnow()

    try:
        db_ok = check_db_connection(db)

        ts_dt = utcnow()
        elapsed_ms = (utcnow() - start).total_seconds() * 1000

        # ✅ Payload JSON (contrato estable, UTC)
        payload: dict[str, Any] = {
            "status": "ok" if db_ok else "degraded",
            "timestamp_utc": ts_dt.isoformat(),
            "elapsed_ms": round(elapsed_ms, 2),
            "db": {"ok": db_ok},
        }

        if db_ok:
            entities = check_core_entities(db)
            payload["core_entities"] = entities
            logger.info("[HEALTH] status=%s db_ok=%s entities=%s", payload["status"], db_ok, entities)
        else:
            logger.warning("[HEALTH] status=degraded db_ok=false")

        if _wants_json(request):
            return JSONResponse(status_code=200, content=payload)

        # ✅ View model HTML (Chile)
        payload_for_view = {**payload, "timestamp_cl": cl_datetime(ts_dt)}
        health_view = _build_health_view(payload_for_view)

        return templates.TemplateResponse(
            "app/superadmin_health.html",
            {
                "request": request,
                "health": payload,        # por si lo usas
                "health_view": health_view,
                "user": user,             # ✅ arregla topbar "Iniciar sesión"
            },
        )

    except Exception as exc:
        logger.exception("[HEALTH] error")

        ts_dt = utcnow()

        # ✅ Payload JSON error (contrato estable, UTC)
        err_payload: dict[str, Any] = {
            "status": "error",
            "timestamp_utc": ts_dt.isoformat(),
            "error": str(exc),
        }

        if _wants_json(request):
            return JSONResponse(status_code=500, content=err_payload)

        payload_for_view = {**err_payload, "timestamp_cl": cl_datetime(ts_dt), "elapsed_ms": None}
        health_view = _build_health_view(payload_for_view)

        return templates.TemplateResponse(
            "app/superadmin_health.html",
            {
                "request": request,
                "health": err_payload,
                "health_view": health_view,
                "user": user,
            },
            status_code=500,
        )


# ============================
# SMOKE TEST
# ============================

@router.get("/health/smoke", response_class=HTMLResponse)
async def health_smoke(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_roles_dep("superadmin")),
):
    start = utcnow()

    try:
        smoke = run_smoke_test(db)

        ts_dt = utcnow()
        elapsed_ms = (utcnow() - start).total_seconds() * 1000

        # ✅ Payload JSON crudo (contrato estable, UTC)
        payload: dict[str, Any] = {
            "status": smoke.get("status", "unknown"),
            "elapsed_ms": round(elapsed_ms, 2),
            "checks": smoke.get("checks", []),
            "timestamp_utc": ts_dt.isoformat(),
        }

        logger.info(
            "[HEALTH][SMOKE] status=%s elapsed_ms=%s checks=%s",
            payload["status"],
            payload["elapsed_ms"],
            len(payload.get("checks") or []),
        )

        if _wants_json(request):
            return JSONResponse(status_code=200, content=payload)

        # ✅ View model HTML (checks normalizados + Chile)
        view_payload = {
            **payload,
            "timestamp_cl": cl_datetime(ts_dt),
            "checks": _normalize_smoke_checks_for_view(payload.get("checks", [])),
        }
        smoke_view = _build_health_view(view_payload)

        return templates.TemplateResponse(
            "app/superadmin_smoke.html",
            {
                "request": request,
                "smoke": view_payload,     # si el template lo usa
                "smoke_view": smoke_view,  # recomendado para imprimir strings CL
                "user": user,              # ✅ topbar
            },
        )

    except Exception as exc:
        logger.exception("[HEALTH][SMOKE] error")

        ts_dt = utcnow()
        elapsed_ms = (utcnow() - start).total_seconds() * 1000

        err_payload: dict[str, Any] = {
            "status": "error",
            "elapsed_ms": round(elapsed_ms, 2),
            "error": str(exc),
            "timestamp_utc": ts_dt.isoformat(),
            "checks": [],
        }

        if _wants_json(request):
            return JSONResponse(status_code=500, content=err_payload)

        view_payload = {**err_payload, "timestamp_cl": cl_datetime(ts_dt), "checks": []}
        smoke_view = _build_health_view(view_payload)

        return templates.TemplateResponse(
            "app/superadmin_smoke.html",
            {
                "request": request,
                "smoke": err_payload,
                "smoke_view": smoke_view,
                "user": user,
            },
            status_code=500,
        )
