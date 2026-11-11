from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from .gst import ZERO, q2


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def trial_balance_from_rows(rows: Iterable[tuple[str, str, str, object, object]]) -> dict[str, object]:
    items = []
    total_debit = ZERO
    total_credit = ZERO
    for code, name, account_type, debit, credit in sorted(rows, key=lambda row: row[0]):
        net = _decimal(debit) - _decimal(credit)
        if net == ZERO:
            continue
        debit_balance = net if net > ZERO else ZERO
        credit_balance = -net if net < ZERO else ZERO
        total_debit += debit_balance
        total_credit += credit_balance
        items.append({
            "account_code": code,
            "account_name": name,
            "account_type": account_type,
            "debit": debit_balance,
            "credit": credit_balance,
        })
    return {
        "items": items,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "is_balanced": total_debit == total_credit,
    }


def profit_and_loss_from_rows(rows: Iterable[tuple[str, object, object]]) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = {}
    for account_type, debit, credit in rows:
        balances[account_type] = _decimal(debit) - _decimal(credit)
    revenue = -balances.get("income", ZERO)
    cogs = balances.get("cogs", ZERO)
    expenses = balances.get("expense", ZERO)
    gross_profit = revenue - cogs
    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "expenses": expenses,
        "net_profit_before_income_tax": gross_profit - expenses,
    }


def running_ledger(
    opening_balance: object,
    events: Sequence[tuple[date, int, str, str, object, str]],
    date_from: date | None = None,
    date_to: date | None = None,
    debit_positive: bool = True,
) -> dict[str, object]:
    sign = Decimal("1") if debit_positive else Decimal("-1")
    opening = _decimal(opening_balance)
    ordered = sorted(events, key=lambda item: (item[0], item[1], item[2]))
    if date_from is not None:
        opening += sum(
            (sign * _decimal(amount) * (1 if direction == "debit" else -1)
             for event_date, _, _, _, amount, direction in ordered if event_date < date_from),
            ZERO,
        )
    selected = [
        event for event in ordered
        if (date_from is None or event[0] >= date_from) and (date_to is None or event[0] <= date_to)
    ]
    balance = opening
    items = []
    for event_date, event_id, kind, reference, amount, direction in selected:
        signed_amount = sign * _decimal(amount) * (1 if direction == "debit" else -1)
        balance += signed_amount
        items.append({
            "date": event_date.isoformat(),
            "id": event_id,
            "type": kind,
            "reference": reference,
            "debit": _decimal(amount) if direction == "debit" else ZERO,
            "credit": _decimal(amount) if direction == "credit" else ZERO,
            "balance": balance,
        })
    return {"opening_balance": opening, "items": items, "closing_balance": balance}


def ageing_report(items: Iterable[Mapping[str, object]], as_of: date) -> dict[str, object]:
    buckets = {
        "current": ZERO,
        "1_30_days": ZERO,
        "31_60_days": ZERO,
        "61_90_days": ZERO,
        "over_90_days": ZERO,
    }
    results = []
    total = ZERO
    for item in items:
        amount = max(ZERO, _decimal(item["outstanding"]))
        due_date = item.get("due_date")
        days_overdue = max(0, (as_of - due_date).days) if isinstance(due_date, date) else 0
        bucket = (
            "current" if days_overdue == 0 else
            "1_30_days" if days_overdue <= 30 else
            "31_60_days" if days_overdue <= 60 else
            "61_90_days" if days_overdue <= 90 else
            "over_90_days"
        )
        buckets[bucket] += amount
        total += amount
        results.append({
            **item,
            "days_overdue": days_overdue,
            "ageing_bucket": bucket,
            "outstanding": amount,
        })
    results.sort(key=lambda item: (item["days_overdue"], str(item.get("number", ""))), reverse=True)
    return {"as_of": as_of.isoformat(), "items": results, "buckets": buckets, "total_outstanding": total}


def gst_summary(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    totals: dict[str, dict[str, Decimal]] = {
        "sales": {key: ZERO for key in ("taxable", "cgst", "sgst", "igst", "cess")},
        "purchases": {key: ZERO for key in ("taxable", "cgst", "sgst", "igst", "cess")},
    }
    for row in rows:
        side = str(row["side"])
        target = totals[side]
        for key in target:
            target[key] += _decimal(row.get(key, ZERO))
    net = {
        key: totals["sales"][key] - totals["purchases"][key]
        for key in ("cgst", "sgst", "igst", "cess")
    }
    return {"sales": totals["sales"], "purchases": totals["purchases"], "net_tax_payable": net}


def inventory_valuation(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    items = []
    total_value = ZERO
    unvalued_items = 0
    for row in rows:
        quantity = _decimal(row["quantity"])
        unit_cost_value = row.get("average_unit_cost")
        unit_cost = _decimal(unit_cost_value) if unit_cost_value is not None else None
        value = q2(quantity * unit_cost) if unit_cost is not None else None
        if value is None:
            unvalued_items += 1
        else:
            total_value += value
        items.append({**row, "value": value})
    return {"items": items, "unvalued_items": unvalued_items, "total_value": total_value}
