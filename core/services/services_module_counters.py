# core/services/services_module_counters.py
"""
Module Counters – ORBION SaaS (baseline aligned)

Objetivo:
- Unificar cómo se leen límites/uso/remaining desde snapshot
- Resolver used/limit/pct de forma robusta (ints/floats/strings)
- Soportar unidades (count vs MB)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _to_float(x: Any) -> float:
    try:
        if x is None or x == "":
            return 0.0
        return float(str(x).strip().replace(",", "."))
    except Exception:
        return 0.0


def _get_dict(d: Any) -> dict:
    return d if isinstance(d, dict) else {}


def _pct(used: float, lim: float) -> float:
    if lim <= 0:
        return 0.0
    p = (used / lim) * 100.0
    if p < 0:
        return 0.0
    if p > 999:
        return 999.0
    return p


def _infer_unit_from_key(key: str) -> str:
    k = (key or "").strip().lower()
    if k.endswith("_mb") or "mb" in k:
        return "mb"
    return "count"


def _label_from_key(key: str) -> str:
    k = (key or "").strip().replace("-", "_").replace(".", "_")
    k = " ".join([p for p in k.split("_") if p])
    return (k[:1].upper() + k[1:]) if k else "Límite"


@dataclass
class CounterUI:
    key: str
    label: str
    unit: str  # "count" | "mb"
    used: float
    limit: float
    pct: float
    is_limited: bool


def resolve_used_limit_from_snapshot(mod: dict, key: str) -> tuple[float, float]:
    """
    Preferencia:
      1) limits[key] + usage[key]
      2) limits[key] + remaining[key] => used = limit - remaining
      3) si no hay limit => (0,0)
    """
    mod = _get_dict(mod)
    limits = _get_dict(mod.get("limits"))
    usage = _get_dict(mod.get("usage"))
    remaining = _get_dict(mod.get("remaining"))

    if key not in limits:
        return 0.0, 0.0

    lim = _to_float(limits.get(key, 0))
    used: float | None = None

    if key in usage:
        used = _to_float(usage.get(key, 0))

    if used is None and key in remaining:
        rem = _to_float(remaining.get(key, 0))
        used = max(lim - rem, 0.0)

    if used is None:
        used = 0.0

    return float(used), float(lim)


def build_counters_for_ui(mod: dict) -> list[CounterUI]:
    """
    Builder genérico:
    - recorre keys en limits
    - label/unit automáticos
    - solo devuelve contadores relevantes (limit>0 o used>0)
    """
    mod = _get_dict(mod)
    limits = _get_dict(mod.get("limits"))
    keys = sorted(list(limits.keys()))

    out: list[CounterUI] = []
    for key in keys:
        used, lim = resolve_used_limit_from_snapshot(mod, key)
        unit = _infer_unit_from_key(key)
        c = CounterUI(
            key=key,
            label=_label_from_key(key),
            unit=unit,
            used=float(used),
            limit=float(lim),
            pct=_pct(float(used), float(lim)),
            is_limited=(lim > 0),
        )
        if c.limit > 0 or c.used > 0:
            out.append(c)

    return out


def build_inbound_counters_for_ui(mod_inbound: dict) -> list[CounterUI]:
    """
    Baseline: keys típicos inbound en orden enterprise.
    Ajusta aquí si tu snapshot usa otros nombres.
    """
    mod_inbound = _get_dict(mod_inbound)

    spec: list[tuple[str, str, str]] = [
        ("recepciones_mes", "Recepciones", "count"),
        ("incidencias_mes", "Incidencias", "count"),
        ("pallets_mes", "Pallets", "count"),
        ("proveedores", "Proveedores", "count"),
        ("evidencias_mb", "Evidencias", "mb"),
    ]

    out: list[CounterUI] = []
    for key, label, unit in spec:
        used, lim = resolve_used_limit_from_snapshot(mod_inbound, key)
        c = CounterUI(
            key=key,
            label=label,
            unit=unit,
            used=float(used),
            limit=float(lim),
            pct=_pct(float(used), float(lim)),
            is_limited=(lim > 0),
        )
        if c.limit > 0 or c.used > 0:
            out.append(c)

    return out
