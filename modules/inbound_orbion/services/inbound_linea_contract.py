# modules/inbound_orbion/services/services_inbound_contract.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InboundLineaModo(str, Enum):
    CANTIDAD = "CANTIDAD"
    PESO = "PESO"


class InboundLineaContractError(Exception):
    """Error de contrato (semántica/consistencia) de una línea inbound."""


@dataclass(frozen=True)
class InboundLineaView:
    # oficial
    modo: InboundLineaModo
    base_cantidad: float | None
    base_peso_kg: float | None
    recibido_cantidad: float | None
    recibido_peso_kg: float | None

    # conversión “dual-language”
    peso_unitario_kg: float | None
    unidades_por_bulto: int | None
    peso_por_bulto_kg: float | None
    bulto_nombre: str | None

    # derivados (estimaciones) - no reemplazan la métrica oficial
    derivado_peso_kg_desde_cantidad: float | None
    derivado_cantidad_desde_peso_kg: float | None
    derivado_peso_kg_desde_bultos: float | None
    derivado_cantidad_desde_bultos: float | None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        if not s.isdigit():
            return None
        return int(s)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _get_attr(obj: Any, name: str) -> Any:
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name)
    return None


def _resolver_conversion_desde_linea_y_producto(linea: Any) -> tuple[float | None, int | None, float | None, str | None]:
    """
    Regla enterprise:
    - Overrides de línea > valores del producto
    - Si no hay datos, devuelve None.
    """
    prod = _get_attr(linea, "producto")

    peso_unit_override = _to_float(_get_attr(linea, "peso_unitario_kg_override"))
    unid_bulto_override = _to_int(_get_attr(linea, "unidades_por_bulto_override"))
    peso_bulto_override = _to_float(_get_attr(linea, "peso_por_bulto_kg_override"))
    bulto_nombre_override = _to_str(_get_attr(linea, "bulto_nombre_override"))

    peso_unit_prod = _to_float(_get_attr(prod, "peso_unitario_kg")) if prod is not None else None
    unid_bulto_prod = _to_int(_get_attr(prod, "unidades_por_bulto")) if prod is not None else None
    peso_bulto_prod = _to_float(_get_attr(prod, "peso_por_bulto_kg")) if prod is not None else None
    bulto_nombre_prod = _to_str(_get_attr(prod, "bulto_nombre")) if prod is not None else None

    peso_unit = peso_unit_override if (peso_unit_override is not None and peso_unit_override > 0) else peso_unit_prod
    unid_bulto = unid_bulto_override if (unid_bulto_override is not None and unid_bulto_override > 0) else unid_bulto_prod
    peso_bulto = peso_bulto_override if (peso_bulto_override is not None and peso_bulto_override > 0) else peso_bulto_prod
    bulto_nombre = bulto_nombre_override or bulto_nombre_prod

    return peso_unit, unid_bulto, peso_bulto, bulto_nombre


def _derivar_desde_conversion(
    *,
    peso_unitario_kg: float | None,
    unidades_por_bulto: int | None,
    peso_por_bulto_kg: float | None,
    bultos: float | int | None,
    cantidad: float | None,
    kg: float | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """
    Devuelve:
    - kg_desde_cantidad
    - cantidad_desde_kg
    - kg_desde_bultos
    - cantidad_desde_bultos
    """
    kg_desde_cantidad = None
    cantidad_desde_kg = None
    kg_desde_bultos = None
    cantidad_desde_bultos = None

    # cantidad <-> kg (si hay peso_unitario_kg)
    if peso_unitario_kg is not None and peso_unitario_kg > 0:
        if cantidad is not None and cantidad >= 0:
            kg_desde_cantidad = round(float(cantidad) * float(peso_unitario_kg), 6)
        if kg is not None and kg >= 0:
            cantidad_desde_kg = round(float(kg) / float(peso_unitario_kg), 6)

    # bultos -> cantidad (si hay unidades_por_bulto)
    b = _to_float(bultos)
    if b is not None and b >= 0 and unidades_por_bulto is not None and unidades_por_bulto > 0:
        cantidad_desde_bultos = round(float(b) * float(unidades_por_bulto), 6)

    # bultos -> kg (si hay peso_por_bulto_kg, o fallback a unidades_por_bulto * peso_unitario_kg)
    if b is not None and b >= 0:
        if peso_por_bulto_kg is not None and peso_por_bulto_kg > 0:
            kg_desde_bultos = round(float(b) * float(peso_por_bulto_kg), 6)
        elif (unidades_por_bulto is not None and unidades_por_bulto > 0) and (peso_unitario_kg is not None and peso_unitario_kg > 0):
            kg_desde_bultos = round(float(b) * float(unidades_por_bulto) * float(peso_unitario_kg), 6)

    return kg_desde_cantidad, cantidad_desde_kg, kg_desde_bultos, cantidad_desde_bultos


def normalizar_linea(linea: Any, *, allow_draft: bool = False) -> InboundLineaView:
    """
    Contrato enterprise (oficial):
    - base cantidad: cantidad_documento
    - base kg: peso_kg
    - recibido cantidad: cantidad_recibida
    - recibido kg: peso_recibido_kg
    - modo:
        * si cantidad_documento > 0 => CANTIDAD
        * elif peso_kg > 0 => PESO
        * else => error (o borrador si allow_draft)

    Nuevo:
    - conversión dual (kg<->unidades) usando override en línea o producto.
    - derivados para UI: no cambian la métrica oficial, solo ayudan a “hablar ambos idiomas”.
    """
    base_cant = _to_float(_get_attr(linea, "cantidad_documento"))
    base_kg = _to_float(_get_attr(linea, "peso_kg"))

    rec_cant = _to_float(_get_attr(linea, "cantidad_recibida"))
    rec_kg = _to_float(_get_attr(linea, "peso_recibido_kg"))

    # Sanitiza negativos
    for name, v in (
        ("cantidad_documento", base_cant),
        ("peso_kg", base_kg),
        ("cantidad_recibida", rec_cant),
        ("peso_recibido_kg", rec_kg),
    ):
        if v is not None and v < 0:
            raise InboundLineaContractError(f"{name} no puede ser negativo.")

    # Decide modo oficial
    modo: InboundLineaModo | None = None
    if base_cant is not None and base_cant > 0:
        modo = InboundLineaModo.CANTIDAD
    elif base_kg is not None and base_kg > 0:
        modo = InboundLineaModo.PESO

    if modo is None:
        if allow_draft:
            # borrador: si hay algún recibido, inferimos
            if rec_kg is not None and rec_kg > 0:
                modo = InboundLineaModo.PESO
            else:
                modo = InboundLineaModo.CANTIDAD
        else:
            raise InboundLineaContractError(
                "La línea no tiene objetivo documental: define cantidad_documento (>0) o peso_kg (>0)."
            )

    # Resolver conversión (línea override > producto)
    peso_unitario_kg, unidades_por_bulto, peso_por_bulto_kg, bulto_nombre = _resolver_conversion_desde_linea_y_producto(linea)
    bultos = _get_attr(linea, "bultos")

    # Derivados según base oficial (y/o bultos)
    kg_desde_cantidad, cant_desde_kg, kg_desde_bultos, cant_desde_bultos = _derivar_desde_conversion(
        peso_unitario_kg=peso_unitario_kg,
        unidades_por_bulto=unidades_por_bulto,
        peso_por_bulto_kg=peso_por_bulto_kg,
        bultos=bultos,
        cantidad=base_cant,
        kg=base_kg,
    )

    # Reglas de consistencia por modo
    if modo == InboundLineaModo.CANTIDAD:
        if not allow_draft and not (base_cant is not None and base_cant > 0):
            raise InboundLineaContractError("Modo CANTIDAD requiere cantidad_documento > 0.")

        return InboundLineaView(
            modo=modo,
            base_cantidad=base_cant if (base_cant is not None and base_cant > 0) else None,
            base_peso_kg=base_kg if (base_kg is not None and base_kg > 0) else None,  # informativo
            recibido_cantidad=rec_cant,
            recibido_peso_kg=None,  # oficial: no aplica (aunque exista en DB)
            peso_unitario_kg=peso_unitario_kg,
            unidades_por_bulto=unidades_por_bulto,
            peso_por_bulto_kg=peso_por_bulto_kg,
            bulto_nombre=bulto_nombre,
            derivado_peso_kg_desde_cantidad=kg_desde_cantidad,
            derivado_cantidad_desde_peso_kg=cant_desde_kg,
            derivado_peso_kg_desde_bultos=kg_desde_bultos,
            derivado_cantidad_desde_bultos=cant_desde_bultos,
        )

    # modo PESO
    if not allow_draft and not (base_kg is not None and base_kg > 0):
        raise InboundLineaContractError("Modo PESO requiere peso_kg > 0.")

    return InboundLineaView(
        modo=modo,
        base_cantidad=base_cant if (base_cant is not None and base_cant > 0) else None,  # informativo
        base_peso_kg=base_kg if (base_kg is not None and base_kg > 0) else None,
        recibido_cantidad=None,  # oficial: no aplica
        recibido_peso_kg=rec_kg,
        peso_unitario_kg=peso_unitario_kg,
        unidades_por_bulto=unidades_por_bulto,
        peso_por_bulto_kg=peso_por_bulto_kg,
        bulto_nombre=bulto_nombre,
        derivado_peso_kg_desde_cantidad=kg_desde_cantidad,
        derivado_cantidad_desde_peso_kg=cant_desde_kg,
        derivado_peso_kg_desde_bultos=kg_desde_bultos,
        derivado_cantidad_desde_bultos=cant_desde_bultos,
    )
