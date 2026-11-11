"""Inventory valuation helpers using a perpetual weighted-average unit cost."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .gst import ZERO, to_decimal

FOUR_PLACES = Decimal("0.0001")
TWO_PLACES = Decimal("0.01")


def weighted_average_cost(
    on_hand_quantity: Decimal,
    current_unit_cost: Decimal | None,
    received_quantity: Decimal,
    received_unit_cost: Decimal,
) -> Decimal:
    """Return the post-receipt moving average; recoverable GST is excluded."""
    on_hand_quantity = to_decimal(on_hand_quantity, "on_hand_quantity")
    received_quantity = to_decimal(received_quantity, "received_quantity")
    received_unit_cost = to_decimal(received_unit_cost, "received_unit_cost")
    if on_hand_quantity < ZERO or received_quantity <= ZERO or received_unit_cost < ZERO:
        raise ValueError("quantities and unit cost must be non-negative, with a positive receipt")
    if on_hand_quantity and current_unit_cost is None:
        raise ValueError("opening valuation is required before valuing a receipt")
    current_unit_cost = to_decimal(current_unit_cost or ZERO, "current_unit_cost")
    if current_unit_cost < ZERO:
        raise ValueError("current_unit_cost must be non-negative")
    total_quantity = on_hand_quantity + received_quantity
    weighted_value = on_hand_quantity * current_unit_cost + received_quantity * received_unit_cost
    return (weighted_value / total_quantity).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def inventory_value(quantity: Decimal, unit_cost: Decimal | None) -> Decimal | None:
    """Return inventory value, preserving unknown legacy valuation as None."""
    quantity = to_decimal(quantity, "quantity")
    if quantity < ZERO:
        raise ValueError("quantity must be non-negative")
    if quantity == ZERO:
        return Decimal("0.00")
    if unit_cost is None:
        return None
    return (quantity * to_decimal(unit_cost, "unit_cost")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
