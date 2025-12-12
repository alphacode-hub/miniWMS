# services/services_stock.py

from typing import Optional

def calcular_estado_stock(
    stock_total: int,
    stock_min: Optional[int],
    stock_max: Optional[int],
) -> str:
    """
    Retorna el estado del producto según stock_min y stock_max siguiendo la misma
    lógica del dashboard.
    """

    # Caso sin configuración
    if stock_min is None and stock_max is None:
        return "Sin configuración"

    # Crítico si está bajo el mínimo
    if stock_min is not None and stock_total < stock_min:
        return "Crítico"

    # Sobre-stock si está estrictamente por encima del máximo
    if stock_max is not None and stock_total > stock_max:
        return "Sobre-stock"

    # Dentro del rango
    return "OK"


def estado_css(estado: str) -> str:
    """
    CSS estándar según estado de stock. Usado por dashboard y stock.
    """
    if estado == "Crítico":
        return "bg-red-100 text-red-700 border border-red-200"
    if estado == "Sobre-stock":
        return "bg-amber-100 text-amber-700 border border-amber-200"
    if estado == "OK":
        return "bg-emerald-100 text-emerald-700 border border-emerald-200"
    return "bg-slate-200 text-slate-700"
