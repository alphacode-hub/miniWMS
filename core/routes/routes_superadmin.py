# core/routes/routes_superadmin.py
"""
Superadmin Console – ORBION (SaaS enterprise, baseline aligned)

✔ Dashboard global (solo superadmin global)
✔ Gestión de negocios (lista + detalle) con filtros + paginación
✔ Snapshot SaaS canónico (entitlements + subscription overlay + usage/limits)
✔ Acciones SaaS por módulo (source of truth en services_subscriptions):
   - activar (trial)
   - cancelar al fin de período
   - reactivar (unschedule cancel)
   - suspender
   - forzar renovación (renew now)
✔ Job center (renovación global)
✔ Alertas globales
✔ Impersonación ("ver como negocio") + salir modo negocio
✔ Auditoría POR NEGOCIO (desde el detalle del negocio)
✔ Cambio de segmento (baseline SaaS) persiste en negocio.entitlements["segment"]

Notas baseline:
- NO existe "auditoría global multi-negocio": la auditoría pertenece al negocio (tenant real).
- Los jobs globales se registran en logs/observability, y también generan auditoría por negocio
  cuando el job ejecuta acciones sobre suscripciones (actor system con negocio_id).
- Se evita duplicar auditoría en rutas: la auditoría vive en services_* (source of truth).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import cast, func, or_
from sqlalchemy import String as SAString
from sqlalchemy.orm import Session

from core.database import get_db
from core.formatting import cl_date, cl_datetime
from core.logging_config import logger
from core.models import Alerta, Auditoria, Movimiento, Negocio, Producto, Usuario
from core.models.enums import ModuleKey, NegocioEstado
from core.models.saas import SuscripcionModulo
from core.models.time import utcnow
from core.security import (
    _get_session_payload_from_request,
    _set_session_cookie_from_payload,
    require_superadmin_dep,
)
from core.services.services_audit import AuditAction, classify_audit_level, audit
from core.services.services_entitlements import (
    get_entitlements_snapshot,
    normalize_entitlements,
    resolve_entitlements,
)
from core.services.services_subscription_jobs import run_subscriptions_job
from core.services.services_subscriptions import (
    activate_module,
    cancel_subscription_at_period_end,
    renew_subscription,
    suspend_subscription,
    unschedule_cancel,
)
from core.web import templates

router = APIRouter(prefix="/superadmin", tags=["superadmin"])

PAGE_SIZE_DEFAULT = 15
AUDIT_PAGE_SIZE = 15


# =========================================================
# HELPERS
# =========================================================

def _is_impersonating(user: dict) -> bool:
    """
    Compatible con distintas keys:
    - cookie payload usa 'acting_negocio_id'
    - algunos flujos pueden usar 'impersonando_negocio_id'
    """
    return bool(user.get("acting_negocio_id") or user.get("impersonando_negocio_id"))


def _require_superadmin_global(user: dict) -> None:
    """Bloquea operaciones peligrosas si el superadmin está impersonando negocio."""
    if _is_impersonating(user):
        raise HTTPException(status_code=403, detail="No permitido en modo negocio (impersonación).")


def _safe_int(v: str | None, default: int = 1) -> int:
    try:
        return int(v) if v is not None and str(v).strip() else default
    except Exception:
        return default


def _ensure_utc_aware(dt: datetime) -> datetime:
    """Asegura tz-aware UTC. Si viene naive, asumimos UTC."""
    if not isinstance(dt, datetime):
        raise TypeError("dt must be datetime")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_dt(s: Any) -> datetime | None:
    """
    Parse defensivo de ISO datetime.
    - Acepta None / "" / "Z"
    - Retorna tz-aware UTC
    """
    if s is None:
        return None
    ss = str(s).strip()
    if not ss:
        return None
    try:
        ss = ss.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ss)
        return _ensure_utc_aware(dt)
    except Exception:
        return None


def _derive_next_period_end_fallback(n: Negocio) -> datetime | None:
    """
    Fallback legacy si el snapshot no trae period_end:
    - usa plan_fecha_fin si existe
    - si no, estima desde plan_fecha_inicio + plan_renovacion_cada_meses (~30 días/mes)
    """
    dfin: date | None = getattr(n, "plan_fecha_fin", None)
    if dfin:
        return datetime(dfin.year, dfin.month, dfin.day, 0, 0, 0, tzinfo=timezone.utc)

    dini: date | None = getattr(n, "plan_fecha_inicio", None)
    if dini:
        months = int(getattr(n, "plan_renovacion_cada_meses", 1) or 1)
        approx_days = 30 * max(1, months)
        dt0 = datetime(dini.year, dini.month, dini.day, 0, 0, 0, tzinfo=timezone.utc)
        return dt0 + timedelta(days=approx_days)

    return None


def _module_alias(mk: str) -> str:
    """
    Normaliza nombres históricos a claves canónicas.
    Mantenerlo aquí evita dependencias cruzadas.
    """
    mk = (mk or "").strip().lower()
    aliases = {
        "wms_core": "wms",
        "core_wms": "wms",
        "basic_wms": "wms",
        "inbound_orbion": "inbound",
    }
    return aliases.get(mk, mk)


def _module_rollup_from_ent(ent: dict) -> dict:
    """
    Resume módulos desde entitlements (idealmente normalizados):
    - enabled_count
    - blocked_count
    - next_period_end: mínimo end entre módulos enabled (si existe)
    - segment: emprendedor | pyme | enterprise (fallback emprendedor)
    """
    mods = (ent or {}).get("modules") or {}
    seg = (ent or {}).get("segment") or (ent or {}).get("segmento") or "emprendedor"
    seg = str(seg).strip().lower() or "emprendedor"

    enabled_count = 0
    blocked_count = 0
    next_period_end: str | None = None

    for _slug, v in mods.items():
        if not isinstance(v, dict):
            blocked_count += 1
            continue

        status = (v.get("status") or "").strip().lower()
        if status == "coming_soon":
            continue

        enabled = bool(v.get("enabled"))
        if enabled:
            enabled_count += 1
            per = v.get("period") or {}
            end = None
            if isinstance(per, dict):
                end = per.get("to") or per.get("end")
            end = end or v.get("period_end")
            if end:
                end_s = str(end).strip()
                if end_s:
                    if next_period_end is None or end_s < next_period_end:
                        next_period_end = end_s
        else:
            blocked_count += 1

    return {
        "segment": seg,
        "enabled_count": enabled_count,
        "blocked_count": blocked_count,
        "next_period_end": next_period_end,
    }


def _modules_list_for_view_from_snapshot(snapshot: dict) -> list[dict]:
    """
    Convierte snapshot.modules -> lista ordenada para render.
    Snapshot puede traer overlay subscription.period.{start,end} y usage/limits/remaining.
    """
    mods = (snapshot or {}).get("modules") or {}
    out: list[dict] = []

    for slug, v in mods.items():
        if not isinstance(v, dict):
            out.append(
                {
                    "slug": slug,
                    "label": slug,
                    "enabled": False,
                    "status": "unknown",
                    "period_start": None,
                    "period_end": None,
                    "trial_ends_at": None,
                    "cancel_at_period_end": False,
                    "usage": {},
                    "limits": {},
                    "remaining": {},
                }
            )
            continue

        status = (v.get("status") or "unknown").strip().lower()
        if status == "coming_soon":
            continue

        sub = v.get("subscription") or {}
        per_start = None
        per_end = None
        trial_ends = None
        cancel_at_period_end = False

        if isinstance(sub, dict):
            per = sub.get("period") or {}
            if isinstance(per, dict):
                per_start = per.get("start")
                per_end = per.get("end")
            trial_ends = sub.get("trial_ends_at")
            cancel_at_period_end = bool(sub.get("cancel_at_period_end", False))

        # fallback a entitlements.period.{from,to}
        if not per_start or not per_end:
            per2 = v.get("period") or {}
            if isinstance(per2, dict):
                per_start = per_start or per2.get("from") or per2.get("start")
                per_end = per_end or per2.get("to") or per2.get("end")

        out.append(
            {
                "slug": slug,
                "label": (v.get("label") or slug).strip() if isinstance(v.get("label"), str) else slug,
                "enabled": bool(v.get("enabled")),
                "status": status,
                "period_start": str(per_start) if per_start else None,
                "period_end": str(per_end) if per_end else None,
                "trial_ends_at": str(trial_ends) if trial_ends else None,
                "cancel_at_period_end": cancel_at_period_end,
                "usage": (v.get("usage") or {}) if isinstance(v.get("usage"), dict) else {},
                "limits": (v.get("limits") or {}) if isinstance(v.get("limits"), dict) else {},
                "remaining": (v.get("remaining") or {}) if isinstance(v.get("remaining"), dict) else {},
            }
        )

    out.sort(key=lambda x: (not x["enabled"], x["slug"]))
    return out

def _mk_from_str(module_key: str) -> ModuleKey:
    """
    Convierte cualquier representación de módulo a ModuleKey canónico.
    Soporta aliases históricos y slugs técnicos.
    """
    if not module_key:
        raise HTTPException(status_code=404, detail="Módulo no válido")

    s = module_key.strip().lower()

    # 1) limpiar paths raros: core.wms / modules/wms / etc
    if "/" in s:
        s = s.split("/")[-1]
    if "." in s:
        s = s.split(".")[-1]

    # 2) normalizar alias histórico -> canónico
    s = _module_alias(s)

    # 3) convertir a enum (source of truth)
    try:
        return ModuleKey(s)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Módulo no válido: {module_key}",
        )


def _get_sub_or_404(db: Session, negocio_id: int, mk: ModuleKey) -> SuscripcionModulo:
    sub = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .filter(SuscripcionModulo.module_key == mk)
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No existe suscripción para este módulo.")
    return sub


def _normalize_segment(seg: str) -> str:
    s = (seg or "").strip().lower()
    aliases = {
        "entrepreneur": "emprendedor",
        "startup": "emprendedor",
        "smb": "pyme",
        "pymes": "pyme",
        "mid": "pyme",
        "midmarket": "pyme",
        "ent": "enterprise",
        "corp": "enterprise",
        "corporate": "enterprise",
    }
    s = aliases.get(s, s)
    if s not in {"emprendedor", "pyme", "enterprise"}:
        raise HTTPException(status_code=400, detail="Segmento inválido.")
    return s


def _get_negocio_entitlements_obj(n: Negocio) -> dict:
    """
    Lee entitlements desde la mejor fuente disponible.
    Soporta columnas JSON(dict) o Text(str con JSON).
    """
    for attr in ("entitlements", "entitlements_json", "entitlements_override"):
        if not hasattr(n, attr):
            continue
        raw = getattr(n, attr, None)
        if raw is None:
            continue
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


def _write_entitlements_to_attr(n: Negocio, attr: str, ent: dict) -> None:
    if not hasattr(n, attr):
        return
    cur = getattr(n, attr, None)
    if isinstance(cur, dict):
        setattr(n, attr, ent)
        return
    if isinstance(cur, str) or cur is None:
        setattr(n, attr, json.dumps(ent, ensure_ascii=False))
        return
    try:
        setattr(n, attr, ent)
    except Exception:
        setattr(n, attr, json.dumps(ent, ensure_ascii=False))


def _set_negocio_entitlements_obj(n: Negocio, ent: dict) -> None:
    wrote_any = False
    for attr in ("entitlements", "entitlements_json", "entitlements_override"):
        if hasattr(n, attr):
            _write_entitlements_to_attr(n, attr, ent)
            wrote_any = True
    if not wrote_any:
        raise RuntimeError(
            "Negocio no tiene columna para entitlements (entitlements/entitlements_json/entitlements_override)."
        )


def _safe_parse_date_ymd(s: str) -> datetime | None:
    """Parse yyyy-mm-dd como UTC tz-aware (inicio del día)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _safe_parse_date_ymd_end(s: str) -> datetime | None:
    """Parse yyyy-mm-dd como UTC tz-aware (fin del día)."""
    dt = _safe_parse_date_ymd(s)
    if not dt:
        return None
    return dt.replace(hour=23, minute=59, second=59)


def _actor_from_user(user: dict) -> dict:
    """
    Actor canónico para services_* (audit v2.1).
    - No fuerza negocio_id aquí (se pasa explícito cuando corresponde).
    """
    return {
        "email": user.get("email") or user.get("usuario") or user.get("user") or "superadmin",
        "role": user.get("rol") or user.get("role") or "superadmin",
        "user_id": user.get("id") or user.get("user_id"),
        "tenant_type": user.get("tenant_type") or "system",
    }


# =========================================================
# DASHBOARD
# =========================================================
@router.get("/dashboard", response_class=HTMLResponse)
async def superadmin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    # Si el superadmin está impersonando, lo mandamos al dashboard del negocio
    if _is_impersonating(user):
        return RedirectResponse(url="/dashboard", status_code=302)

    total_negocios = db.query(Negocio).count()
    negocios_activos = db.query(Negocio).filter(Negocio.estado == NegocioEstado.ACTIVO).count()
    negocios_suspendidos = db.query(Negocio).filter(Negocio.estado == NegocioEstado.SUSPENDIDO).count()
    alertas_pendientes = db.query(Alerta).filter(Alerta.estado == "pendiente").count()

    logger.info("[SUPERADMIN] dashboard total_negocios=%s", total_negocios)

    return templates.TemplateResponse(
        "app/superadmin_dashboard.html",
        {
            "request": request,
            "user": user,
            "total_negocios": total_negocios,
            "negocios_activos": negocios_activos,
            "negocios_suspendidos": negocios_suspendidos,
            "alertas_pendientes": alertas_pendientes,
        },
    )


# =========================================================
# JOB CENTER
# =========================================================
@router.get("/jobs", response_class=HTMLResponse)
async def superadmin_jobs(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    jobs = [
        {
            "slug": "subscriptions_job",
            "label": "Subscriptions Job (Contrato v1)",
            "description": (
                "Procesa suscripciones a nivel global: expira trials, marca past_due, "
                "repara inconsistencias y renueva períodos pagados. "
                "Respeta cancel_at_period_end."
            ),
            "method": "POST",
            "action": "/superadmin/jobs/subscriptions",
        },
    ]

    return templates.TemplateResponse(
        "app/superadmin_jobs.html",
        {"request": request, "user": user, "jobs": jobs},
    )


@router.post("/jobs/subscriptions", response_class=HTMLResponse)
async def superadmin_job_subscriptions(
    request: Request,
    batch_size: int = Form(500),
    lookahead_minutes: int = Form(5),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    bs = max(10, min(int(batch_size or 500), 5000))
    la = max(0, min(int(lookahead_minutes or 5), 1440))

    try:
        res = run_subscriptions_job(db, batch_size=bs, lookahead_minutes=la, commit=True)
    except Exception:
        logger.exception("[SUPERADMIN][JOB] subscriptions_job crashed")
        raise

    counters = (res or {}).get("counters") or {}

    logger.info(
        "[SUPERADMIN][JOB] subscriptions_job done scanned=%s renewed=%s past_due=%s cancelled=%s trial_expired=%s repaired=%s errors=%s",
        counters.get("scanned"),
        counters.get("renewed"),
        counters.get("past_due"),
        counters.get("cancelled"),
        counters.get("trial_expired"),
        counters.get("repaired"),
        counters.get("errors"),
    )

    return templates.TemplateResponse(
        "app/superadmin_job_result.html",
        {
            "request": request,
            "user": user,
            "job_name": "Subscriptions Job (Contrato v1)",
            "result": res,
        },
    )


# =========================================================
# NEGOCIOS LISTA (filtros + paginación)
# =========================================================
@router.get("/negocios", response_class=HTMLResponse)
async def superadmin_negocios(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    params = request.query_params
    q = (params.get("q") or "").strip()
    estado = (params.get("estado") or "").strip().lower()  # activo/suspendido
    module_filter = _module_alias((params.get("module") or "").strip().lower())  # wms/inbound/...
    page = _safe_int(params.get("page"), 1)
    page_size = _safe_int(params.get("page_size"), PAGE_SIZE_DEFAULT)

    page = max(1, page)
    page_size = min(max(5, page_size), 50)

    base_query = db.query(Negocio)

    # Filtro estado
    if estado in {"activo", "suspendido"}:
        base_query = base_query.filter(
            Negocio.estado == (NegocioEstado.ACTIVO if estado == "activo" else NegocioEstado.SUSPENDIDO)
        )

    # Búsqueda por nombre o ID
    if q:
        like_expr = f"%{q.lower()}%"
        base_query = base_query.filter(
            or_(
                func.lower(Negocio.nombre_fantasia).like(like_expr),
                cast(Negocio.id, SAString).like(f"%{q}%"),
            )
        )

    total = base_query.count()
    total_pages = max(1, math.ceil(total / page_size))
    if page > total_pages:
        page = total_pages

    negocios = (
        base_query.order_by(Negocio.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )

    hace_30 = utcnow() - timedelta(days=30)

    data: list[dict] = []
    for n in negocios:
        # métricas
        usuarios = db.query(Usuario).filter(Usuario.negocio_id == n.id).count()
        productos = db.query(Producto).filter(Producto.negocio_id == n.id).count()
        movimientos = db.query(Movimiento).filter(Movimiento.negocio_id == n.id, Movimiento.fecha >= hace_30).count()

        # Snapshot canónico (overlay + usage/limits/remaining)
        snap = get_entitlements_snapshot(db, n.id)

        # Segmento y resumen desde resolve (fuente viva)
        ent_live = resolve_entitlements(n) or {}
        roll = _module_rollup_from_ent(ent_live)

        # filtro por módulo enabled (si el usuario lo pide)
        if module_filter:
            mods = (snap or {}).get("modules") or {}
            mod = mods.get(module_filter)
            enabled = bool(mod and mod.get("enabled"))
            if not enabled:
                continue

        # Próximo corte:
        dt_next = _parse_iso_dt(roll.get("next_period_end"))
        if not dt_next:
            dt_next = _derive_next_period_end_fallback(n)
        next_period_end_cl = cl_date(dt_next) if dt_next else "—"

        # Último acceso
        dt_last = n.ultimo_acceso or getattr(n, "updated_at", None) or getattr(n, "created_at", None)
        ultimo_acceso_cl = cl_datetime(dt_last, with_tz=False) if dt_last else "—"

        data.append(
            {
                "id": n.id,
                "nombre": n.nombre_fantasia,
                "estado": getattr(n.estado, "value", n.estado),
                "segmento": roll["segment"],
                "modulos_activos": roll["enabled_count"],
                "modulos_bloqueados": roll["blocked_count"],
                "next_period_end": roll.get("next_period_end"),
                "next_period_end_cl": next_period_end_cl,
                "ultimo_acceso": n.ultimo_acceso,
                "ultimo_acceso_cl": ultimo_acceso_cl,
                "usuarios": usuarios,
                "productos": productos,
                "movimientos_30d": movimientos,
            }
        )

    paginacion = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
    }

    return templates.TemplateResponse(
        "app/superadmin_negocios.html",
        {
            "request": request,
            "user": user,
            "negocios": data,
            "filtros": {"q": q, "estado": estado, "module": module_filter, "page_size": page_size},
            "paginacion": paginacion,
        },
    )


# =========================================================
# NEGOCIO DETALLE (snapshot + acciones SaaS)
# =========================================================
@router.get("/negocios/{negocio_id}", response_class=HTMLResponse)
async def superadmin_negocio_detalle(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    snap = get_entitlements_snapshot(db, negocio_id)
    ent_live = resolve_entitlements(negocio) or {}
    roll = _module_rollup_from_ent(ent_live)

    modules = _modules_list_for_view_from_snapshot(snap)

    subs = (
        db.query(SuscripcionModulo)
        .filter(SuscripcionModulo.negocio_id == negocio_id)
        .order_by(SuscripcionModulo.module_key.asc())
        .all()
    )

    eventos = (
        db.query(Auditoria)
        .filter(Auditoria.negocio_id == negocio_id)
        .order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .limit(12)
        .all()
    )

    for e in eventos:
        try:
            setattr(e, "nivel", classify_audit_level(getattr(e, "accion", ""), getattr(e, "detalle", None)))
        except Exception:
            setattr(e, "nivel", "normal")

    usuarios = db.query(Usuario).filter(Usuario.negocio_id == negocio_id).count()
    productos = db.query(Producto).filter(Producto.negocio_id == negocio_id).count()

    return templates.TemplateResponse(
        "app/superadmin_negocio_detalle.html",
        {
            "request": request,
            "user": user,
            "negocio": negocio,
            "segmento": roll["segment"],
            "modulos_activos": roll["enabled_count"],
            "modulos_bloqueados": roll["blocked_count"],
            "next_period_end": roll["next_period_end"],
            "usuarios_count": usuarios,
            "productos_count": productos,
            "modules": modules,
            "snapshot": snap,
            "entitlements": ent_live,
            "subscriptions": subs,
            "eventos_auditoria": eventos,
        },
    )


@router.post("/negocios/{negocio_id}/estado")
async def superadmin_negocio_estado_update(
    negocio_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    estado_norm = (estado or "").strip().lower()
    if estado_norm not in {"activo", "suspendido"}:
        raise HTTPException(status_code=400, detail="Estado inválido.")

    before = getattr(negocio.estado, "value", negocio.estado)

    negocio.estado = NegocioEstado.ACTIVO if estado_norm == "activo" else NegocioEstado.SUSPENDIDO
    db.commit()
    db.refresh(negocio)

    # Auditoría del cambio de estado (evento de negocio)
    audit(
        db,
        action=AuditAction.NEGOCIO_STATE_UPDATE,
        user=_actor_from_user(user),
        negocio_id=negocio.id,
        before={"estado": before},
        after={"estado": estado_norm},
        commit=True,
    )

    logger.info("[SUPERADMIN] update_estado negocio_id=%s estado=%s", negocio_id, estado_norm)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


# =========================================================
# CAMBIO DE SEGMENTO (baseline SaaS)
# =========================================================
@router.post("/negocios/{negocio_id}/segmento")
async def superadmin_negocio_segmento_update(
    negocio_id: int,
    segmento: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    seg = _normalize_segment(segmento)

    ent_raw = _get_negocio_entitlements_obj(negocio)
    ent_norm = normalize_entitlements(ent_raw)

    before = {"segment": (ent_norm.get("segment") or ent_norm.get("segmento"))}

    ent_norm["segment"] = seg
    ent_norm.pop("segmento", None)

    _set_negocio_entitlements_obj(negocio, ent_norm)
    db.commit()
    db.refresh(negocio)

    audit(
        db,
        action=AuditAction.NEGOCIO_SEGMENT_UPDATE,
        user=_actor_from_user(user),
        negocio_id=negocio.id,
        before=before,
        after={"segment": seg},
        commit=True,
    )

    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


# =========================================================
# ACCIONES SaaS POR MÓDULO (desde superadmin)
# - Auditoría vive en services_subscriptions (source of truth)
# =========================================================
@router.post("/negocios/{negocio_id}/modules/{module_key}/activate")
async def superadmin_activate_module(
    negocio_id: int,
    module_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    mk = _mk_from_str(module_key)
    activate_module(
        db,
        negocio_id=negocio_id,
        module_key=mk,
        start_trial=True,
        actor=_actor_from_user(user),
    )
    db.commit()

    logger.info("[SUPERADMIN] module_activate negocio_id=%s module=%s", negocio_id, mk.value)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


@router.post("/negocios/{negocio_id}/modules/{module_key}/cancel")
async def superadmin_cancel_module(
    negocio_id: int,
    module_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    mk = _mk_from_str(module_key)
    sub = _get_sub_or_404(db, negocio_id, mk)

    cancel_subscription_at_period_end(db, sub, actor=_actor_from_user(user))
    db.commit()

    logger.info("[SUPERADMIN] module_cancel negocio_id=%s module=%s", negocio_id, mk.value)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


@router.post("/negocios/{negocio_id}/modules/{module_key}/reactivate")
async def superadmin_reactivate_module(
    negocio_id: int,
    module_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    mk = _mk_from_str(module_key)
    sub = _get_sub_or_404(db, negocio_id, mk)

    unschedule_cancel(db, sub, actor=_actor_from_user(user))
    db.commit()

    logger.info("[SUPERADMIN] module_reactivate negocio_id=%s module=%s", negocio_id, mk.value)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


@router.post("/negocios/{negocio_id}/modules/{module_key}/suspend")
async def superadmin_suspend_module(
    negocio_id: int,
    module_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    mk = _mk_from_str(module_key)
    sub = _get_sub_or_404(db, negocio_id, mk)

    suspend_subscription(db, sub, actor=_actor_from_user(user))
    db.commit()

    logger.info("[SUPERADMIN] module_suspend negocio_id=%s module=%s", negocio_id, mk.value)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


@router.post("/negocios/{negocio_id}/modules/{module_key}/renew-now")
async def superadmin_force_renew_module(
    negocio_id: int,
    module_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    mk = _mk_from_str(module_key)
    sub = _get_sub_or_404(db, negocio_id, mk)

    renew_subscription(db, sub, actor=_actor_from_user(user))
    db.commit()

    logger.info("[SUPERADMIN] module_renew_now negocio_id=%s module=%s", negocio_id, mk.value)
    return RedirectResponse(url=f"/superadmin/negocios/{negocio_id}", status_code=303)


# =========================================================
# ALERTAS GLOBALES
# =========================================================
@router.get("/alertas", response_class=HTMLResponse)
async def superadmin_alertas(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    alertas = (
        db.query(Alerta)
        .join(Negocio, Alerta.negocio_id == Negocio.id)
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id.desc())
        .limit(500)
        .all()
    )

    return templates.TemplateResponse(
        "app/superadmin_alertas.html",
        {"request": request, "user": user, "alertas": alertas},
    )


# =========================================================
# IMPERSONACIÓN
# =========================================================
@router.get("/negocios/{negocio_id}/ver-como")
async def superadmin_ver_como_negocio(
    negocio_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado.")

    payload = _get_session_payload_from_request(request)
    if not payload:
        return RedirectResponse("/app/login", status_code=302)

    payload["acting_negocio_id"] = negocio.id
    payload["acting_negocio_nombre"] = negocio.nombre_fantasia

    resp = RedirectResponse(url="/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)

    audit(
        db,
        action=AuditAction.IMPERSONATION_START,
        user=_actor_from_user(user),
        negocio_id=negocio.id,
        extra={"acting_negocio_nombre": negocio.nombre_fantasia},
        commit=True,
    )

    logger.info("[SUPERADMIN] impersonate negocio_id=%s", negocio.id)
    return resp


@router.get("/salir-modo-negocio")
async def superadmin_salir_modo_negocio(
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    payload = _get_session_payload_from_request(request)
    if not payload:
        return RedirectResponse("/app/login", status_code=302)

    acting_id = payload.get("acting_negocio_id")
    payload.pop("acting_negocio_id", None)
    payload.pop("acting_negocio_nombre", None)

    resp = RedirectResponse(url="/superadmin/dashboard", status_code=302)
    _set_session_cookie_from_payload(resp, payload)

    audit(
        db,
        action=AuditAction.IMPERSONATION_STOP,
        user=_actor_from_user(user),
        negocio_id=int(acting_id) if acting_id else None,
        commit=True,
    )

    logger.info("[SUPERADMIN] exit_impersonation")
    return resp


# =========================================================
# AUDITORÍA POR NEGOCIO
# =========================================================
@router.get("/negocios/{negocio_id}/auditoria", response_class=HTMLResponse)
async def superadmin_auditoria_negocio(
    request: Request,
    negocio_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_superadmin_dep),
):
    _require_superadmin_global(user)

    negocio = db.query(Negocio).filter(Negocio.id == negocio_id).first()
    if not negocio:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    params = request.query_params
    texto = (params.get("q") or "").strip()
    fecha_desde_str = (params.get("desde") or "").strip()
    fecha_hasta_str = (params.get("hasta") or "").strip()
    nivel_str = (params.get("nivel") or "").strip().lower()
    page = max(1, _safe_int(params.get("page"), 1))

    fecha_desde = _safe_parse_date_ymd(fecha_desde_str)
    fecha_hasta = _safe_parse_date_ymd_end(fecha_hasta_str)

    base_query = db.query(Auditoria).filter(Auditoria.negocio_id == negocio_id)

    if fecha_desde:
        base_query = base_query.filter(Auditoria.fecha >= fecha_desde)
    if fecha_hasta:
        base_query = base_query.filter(Auditoria.fecha <= fecha_hasta)

    if texto:
        like_expr = f"%{texto.lower()}%"
        base_query = base_query.filter(
            or_(
                func.lower(Auditoria.usuario).like(like_expr),
                func.lower(Auditoria.accion).like(like_expr),
                func.lower(Auditoria.detalle).like(like_expr),
            )
        )

    total_filtrado = base_query.count()
    total_pages = max(1, math.ceil(total_filtrado / AUDIT_PAGE_SIZE))
    if page > total_pages:
        page = total_pages

    rows = (
        base_query.order_by(Auditoria.fecha.desc(), Auditoria.id.desc())
        .offset((page - 1) * AUDIT_PAGE_SIZE)
        .limit(AUDIT_PAGE_SIZE)
        .all()
    )

    registros: list[Auditoria] = []
    for r in rows:
        try:
            nivel = classify_audit_level(getattr(r, "accion", ""), getattr(r, "detalle", None))
        except Exception:
            nivel = "normal"
        setattr(r, "nivel", nivel)
        registros.append(r)

    if nivel_str in {"critico", "warning", "info", "normal"}:
        registros = [r for r in registros if getattr(r, "nivel", "normal") == nivel_str]

    paginacion = {
        "page": page,
        "page_size": AUDIT_PAGE_SIZE,
        "total": total_filtrado,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
    }

    return templates.TemplateResponse(
        "app/superadmin_auditoria.html",
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
            "paginacion": paginacion,
        },
    )
