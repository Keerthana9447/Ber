from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Inventory,
    InventoryOpeningValuation,
    JournalEntry,
    JournalEntryLine,
    Product,
    StockMovement,
    User,
    Warehouse,
)

CENT = Decimal("0.01")


def _journal_entry(
    session: Session,
    user: User,
    reference: str,
    narration: str,
    lines: list[tuple[str, Decimal, Decimal]],
    reverses_entry_id: int | None = None,
) -> JournalEntry:
    entry = JournalEntry(
        entry_date=date.today(),
        reference=reference,
        narration=narration,
        source_type="inventory_opening_valuation" if reverses_entry_id is None else "inventory_opening_valuation_reversal",
        reverses_entry_id=reverses_entry_id,
        posted_by=user.id,
    )
    session.add(entry)
    session.flush()
    session.add_all(
        JournalEntryLine(
            entry_id=entry.id,
            account_code=account,
            debit=debit,
            credit=credit,
        )
        for account, debit, credit in lines
        if debit > 0 or credit > 0
    )
    return entry


def record_opening_valuation(
    session: Session,
    user: User,
    rows: Sequence[tuple[int, int, Decimal]],
    ip_address: str | None,
) -> dict[str, object]:
    if not rows:
        raise ValueError("At least one product/warehouse valuation is required")
    keys = [(product_id, warehouse_id) for product_id, warehouse_id, _ in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("A product and warehouse can appear only once in a batch")

    inventories = session.scalars(
        select(Inventory)
        .where(
            tuple_(Inventory.product_id, Inventory.warehouse_id).in_(keys)
        )
        .order_by(Inventory.product_id, Inventory.warehouse_id)
        .with_for_update()
    ).all()
    inventory_by_key = {(row.product_id, row.warehouse_id): row for row in inventories}
    if len(inventory_by_key) != len(keys):
        raise ValueError("Every opening valuation requires an existing inventory balance")

    values: list[tuple[Inventory, Product, Warehouse, Decimal, Decimal]] = []
    total_value = Decimal("0.00")
    for product_id, warehouse_id, unit_cost in rows:
        inventory = inventory_by_key[(product_id, warehouse_id)]
        product = session.get(Product, product_id)
        warehouse = session.get(Warehouse, warehouse_id)
        if product is None or product.is_service or warehouse is None:
            raise ValueError("Opening valuation requires a stocked product and warehouse")
        if inventory.quantity <= 0:
            raise ValueError(f"{product.name} has no on-hand stock in {warehouse.name}")
        if inventory.average_unit_cost is not None:
            raise ValueError(f"{product.name} in {warehouse.name} already has a known cost")
        if unit_cost <= 0:
            raise ValueError("Opening unit cost must be greater than zero")
        value = (inventory.quantity * unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
        if value <= 0:
            raise ValueError(f"Opening valuation for {product.name} rounds to zero")
        values.append((inventory, product, warehouse, unit_cost, value))
        total_value += value

    journal = _journal_entry(
        session,
        user,
        f"OPENING-STOCK-{date.today().isoformat()}",
        f"Opening inventory valuation batch ({len(values)} product/warehouse rows)",
        [("1200", total_value, Decimal("0")), ("3000", Decimal("0"), total_value)],
    )
    for inventory, product, warehouse, unit_cost, value in values:
        inventory.average_unit_cost = unit_cost
        session.add(
            InventoryOpeningValuation(
                product_id=inventory.product_id,
                warehouse_id=inventory.warehouse_id,
                quantity=inventory.quantity,
                unit_cost=unit_cost,
                total_value=value,
                journal_entry_id=journal.id,
                created_by=user.id,
            )
        )
    session.add(
        AuditLog(
            user_id=user.id,
            action="set_opening_valuation",
            entity_type="inventory_opening_valuation_batch",
            entity_id=journal.id,
            old_values=None,
            new_values={
                "journal_entry_id": journal.id,
                "rows": [
                    {
                        "product_id": inventory.product_id,
                        "warehouse_id": inventory.warehouse_id,
                        "quantity": format(inventory.quantity, "f"),
                        "unit_cost": format(unit_cost, "f"),
                        "total_value": format(value, "f"),
                    }
                    for inventory, _, _, unit_cost, value in values
                ],
                "total_value": format(total_value, "f"),
            },
            ip_address=ip_address,
        )
    )
    session.flush()
    return {
        "journal_entry_id": journal.id,
        "total_value": format(total_value, "f"),
        "items": [
            {
                "product_id": inventory.product_id,
                "warehouse_id": inventory.warehouse_id,
                "quantity": format(inventory.quantity, "f"),
                "unit_cost": format(unit_cost, "f"),
                "total_value": format(value, "f"),
            }
            for inventory, _, _, unit_cost, value in values
        ],
    }


def reverse_opening_valuation(
    session: Session,
    user: User,
    journal_entry_id: int,
    ip_address: str | None,
) -> dict[str, object]:
    original = session.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.source_type == "inventory_opening_valuation",
        )
        .with_for_update()
    )
    if original is None:
        raise LookupError("Opening valuation batch not found")
    if session.scalar(
        select(JournalEntry.id).where(JournalEntry.reverses_entry_id == original.id).limit(1)
    ) is not None:
        raise ValueError("Opening valuation batch has already been reversed")
    valuations = session.scalars(
        select(InventoryOpeningValuation)
        .where(InventoryOpeningValuation.journal_entry_id == original.id)
        .order_by(InventoryOpeningValuation.product_id, InventoryOpeningValuation.warehouse_id)
    ).all()
    inventories = session.scalars(
        select(Inventory)
        .where(
            tuple_(
                Inventory.product_id,
                Inventory.warehouse_id,
            ).in_([(row.product_id, row.warehouse_id) for row in valuations])
        )
        .order_by(Inventory.product_id, Inventory.warehouse_id)
        .with_for_update()
    ).all()
    inventory_by_key = {(row.product_id, row.warehouse_id): row for row in inventories}
    for valuation in valuations:
        inventory = inventory_by_key.get((valuation.product_id, valuation.warehouse_id))
        if (
            inventory is None
            or inventory.quantity != valuation.quantity
            or inventory.average_unit_cost != valuation.unit_cost
            or session.scalar(
                select(StockMovement.id)
                .where(
                    StockMovement.product_id == valuation.product_id,
                    StockMovement.warehouse_id == valuation.warehouse_id,
                    StockMovement.created_at > valuation.created_at,
                )
                .limit(1)
            ) is not None
        ):
            raise ValueError("A valuation can only be reversed before later stock movements or receipts")
    original_lines = session.scalars(
        select(JournalEntryLine).where(JournalEntryLine.entry_id == original.id).order_by(JournalEntryLine.id)
    ).all()
    reversal_lines = [(line.account_code, line.credit, line.debit) for line in original_lines]
    reversal = _journal_entry(
        session,
        user,
        f"REV-{original.reference}",
        f"Reversal of opening inventory valuation batch {original.id}",
        reversal_lines,
        reverses_entry_id=original.id,
    )
    for inventory in inventories:
        inventory.average_unit_cost = None
    session.add(
        AuditLog(
            user_id=user.id,
            action="reverse_opening_valuation",
            entity_type="inventory_opening_valuation_batch",
            entity_id=original.id,
            old_values={"journal_entry_id": original.id},
            new_values={"reversal_journal_entry_id": reversal.id},
            ip_address=ip_address,
        )
    )
    session.flush()
    return {"journal_entry_id": original.id, "reversal_journal_entry_id": reversal.id}
