"""Double-entry posting rules. Pure functions: they BUILD balanced journal
entries; persisting them is the caller's job (inside the same DB transaction
as the business document).

Scope notes: no input-tax-credit eligibility/blocked-credit logic, no TDS/TCS,
no income tax, no inventory costing method (caller supplies COGS amount).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .gst import InvoiceResult, ZERO, q2, to_decimal


class AccountingError(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str  # asset | liability | equity | income | cogs | expense


DEFAULT_ACCOUNTS = [
    Account("1000", "Cash in Hand", "asset"),
    Account("1010", "Bank", "asset"),
    Account("1100", "Accounts Receivable", "asset"),
    Account("1200", "Inventory", "asset"),
    Account("1300", "Input CGST", "asset"),
    Account("1301", "Input SGST", "asset"),
    Account("1302", "Input IGST", "asset"),
    Account("1303", "Input Cess", "asset"),
    Account("2000", "Accounts Payable", "liability"),
    Account("2100", "Output CGST", "liability"),
    Account("2101", "Output SGST", "liability"),
    Account("2102", "Output IGST", "liability"),
    Account("2103", "Output Cess", "liability"),
    Account("2200", "Customer Advances", "liability"),
    Account("3000", "Owner's Equity", "equity"),
    Account("4000", "Sales", "income"),
    Account("4100", "Sales Returns", "income"),     # contra: normally debit
    Account("4900", "Round Off", "income"),
    Account("5000", "Cost of Goods Sold", "cogs"),
    Account("5100", "Rent", "expense"),
    Account("5110", "Salaries", "expense"),
    Account("5120", "Electricity", "expense"),
    Account("5130", "Transportation", "expense"),
    Account("5140", "Marketing", "expense"),
    Account("5150", "Maintenance", "expense"),
    Account("5160", "Office Supplies", "expense"),
    Account("5170", "Fuel", "expense"),
    Account("5190", "Other Expenses", "expense"),
]
_CODES = {a.code for a in DEFAULT_ACCOUNTS}
CASH, BANK, AR, INVENTORY, AP, ADVANCES = "1000", "1010", "1100", "1200", "2000", "2200"
SALES, SALES_RETURNS, ROUND_OFF, COGS = "4000", "4100", "4900", "5000"
OUTPUT_TAX = {"cgst": "2100", "sgst": "2101", "igst": "2102", "cess": "2103"}
INPUT_TAX = {"cgst": "1300", "sgst": "1301", "igst": "1302", "cess": "1303"}


@dataclass(frozen=True)
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO


@dataclass
class JournalEntry:
    entry_date: date
    reference: str
    narration: str
    lines: list
    reverses: Optional[str] = None

    def __post_init__(self):
        self.validate()

    def validate(self):
        if len(self.lines) < 2:
            raise AccountingError("a journal entry needs at least two lines")
        for l in self.lines:
            if l.account not in _CODES:
                raise AccountingError(f"unknown account {l.account}")
            if l.debit < 0 or l.credit < 0 or (l.debit > 0) == (l.credit > 0):
                raise AccountingError("each line needs exactly one positive debit OR credit")
        d = sum((l.debit for l in self.lines), ZERO)
        c = sum((l.credit for l in self.lines), ZERO)
        if d != c:
            raise AccountingError(f"unbalanced entry: debit {d} != credit {c}")


def _entry(d, ref, narration, raw, reverses=None) -> JournalEntry:
    lines = [Line(a, q2(dr), q2(cr)) for a, dr, cr in raw if q2(dr) > 0 or q2(cr) > 0]
    return JournalEntry(d, ref, narration, lines, reverses)


def _tax_parts(inv: InvoiceResult):
    return {"cgst": inv.cgst_total, "sgst": inv.sgst_total,
            "igst": inv.igst_total, "cess": inv.cess_total}


def _money_account(method_kind: str) -> str:
    if method_kind not in ("cash", "bank"):
        raise AccountingError("method_kind must be 'cash' or 'bank'")
    return CASH if method_kind == "cash" else BANK


def post_sales_invoice(d: date, ref: str, inv: InvoiceResult, cogs=ZERO) -> list:
    cogs = to_decimal(cogs, 'cogs')
    raw = [(AR, inv.grand_total, ZERO), (SALES, ZERO, inv.taxable_total)]
    raw += [(OUTPUT_TAX[k], ZERO, v) for k, v in _tax_parts(inv).items()]
    ro = inv.round_off
    raw.append((ROUND_OFF, ZERO if ro >= 0 else -ro, ro if ro > 0 else ZERO))
    entries = [_entry(d, ref, f"Sales invoice {ref}", raw)]
    if cogs > 0:
        entries.append(_entry(d, ref, f"COGS for {ref}", [(COGS, cogs, ZERO), (INVENTORY, ZERO, cogs)]))
    return entries


def post_credit_note(d: date, ref: str, inv: InvoiceResult, cogs_reversal=ZERO) -> list:
    cogs_reversal = to_decimal(cogs_reversal, 'cogs_reversal')
    raw = [(SALES_RETURNS, inv.taxable_total, ZERO), (AR, ZERO, inv.grand_total)]
    raw += [(OUTPUT_TAX[k], v, ZERO) for k, v in _tax_parts(inv).items()]
    ro = inv.round_off
    raw.append((ROUND_OFF, ro if ro > 0 else ZERO, ZERO if ro >= 0 else -ro))
    entries = [_entry(d, ref, f"Credit note {ref}", raw)]
    if cogs_reversal > 0:
        entries.append(_entry(d, ref, f"Restock for {ref}",
                              [(INVENTORY, cogs_reversal, ZERO), (COGS, ZERO, cogs_reversal)]))
    return entries


def post_purchase_invoice(d: date, ref: str, inv: InvoiceResult) -> JournalEntry:
    raw = [(INVENTORY, inv.taxable_total, ZERO), (AP, ZERO, inv.grand_total)]
    raw += [(INPUT_TAX[k], v, ZERO) for k, v in _tax_parts(inv).items()]
    ro = inv.round_off
    raw.append((ROUND_OFF, ro if ro > 0 else ZERO, ZERO if ro >= 0 else -ro))
    return _entry(d, ref, f"Purchase invoice {ref}", raw)


def post_customer_receipt(d: date, ref: str, amount, method_kind: str, advance=False) -> JournalEntry:
    amount = q2(to_decimal(amount, 'amount'))
    return _entry(d, ref, f"Receipt {ref}",
                  [(_money_account(method_kind), amount, ZERO),
                   (ADVANCES if advance else AR, ZERO, amount)])


def post_customer_refund(d: date, ref: str, amount, method_kind: str, advance=False) -> JournalEntry:
    amount = q2(to_decimal(amount, 'amount'))
    return _entry(d, ref, f"Customer refund {ref}",
                  [(ADVANCES if advance else AR, amount, ZERO),
                   (_money_account(method_kind), ZERO, amount)])


def post_supplier_payment(d: date, ref: str, amount, method_kind: str) -> JournalEntry:
    amount = q2(to_decimal(amount, 'amount'))
    return _entry(d, ref, f"Supplier payment {ref}",
                  [(AP, amount, ZERO), (_money_account(method_kind), ZERO, amount)])


def post_expense(d: date, ref: str, expense_account: str, amount, method_kind: str) -> JournalEntry:
    if not expense_account.startswith("51"):
        raise AccountingError("expense_account must be an expense account (51xx)")
    amount = q2(to_decimal(amount, 'amount'))
    return _entry(d, ref, f"Expense {ref}",
                  [(expense_account, amount, ZERO), (_money_account(method_kind), ZERO, amount)])


def reverse_entry(e: JournalEntry, d: date, reason: str) -> JournalEntry:
    """Controlled reversal: never edit or delete a posted entry."""
    return JournalEntry(d, f"REV-{e.reference}", f"Reversal of {e.reference}: {reason}",
                        [Line(l.account, l.credit, l.debit) for l in e.lines], reverses=e.reference)


def _net_by_account(entries) -> dict:
    net: dict = {}
    for e in entries:
        for l in e.lines:
            net[l.account] = net.get(l.account, ZERO) + l.debit - l.credit
    return net


def trial_balance(entries, accounts=DEFAULT_ACCOUNTS) -> dict:
    net = _net_by_account(entries)
    names = {a.code: a.name for a in accounts}
    rows = []
    for code in sorted(net):
        if net[code] == 0:
            continue
        rows.append((code, names[code], net[code] if net[code] > 0 else ZERO,
                     -net[code] if net[code] < 0 else ZERO))
    return {"rows": rows,
            "total_debit": sum((r[2] for r in rows), ZERO),
            "total_credit": sum((r[3] for r in rows), ZERO)}


def profit_and_loss(entries, accounts=DEFAULT_ACCOUNTS) -> dict:
    net = _net_by_account(entries)
    types = {a.code: a.type for a in accounts}
    revenue = -sum((v for c, v in net.items() if types[c] == "income"), ZERO)
    cogs = sum((v for c, v in net.items() if types[c] == "cogs"), ZERO)
    expenses = sum((v for c, v in net.items() if types[c] == "expense"), ZERO)
    gross = revenue - cogs
    return {"net_revenue": revenue, "cogs": cogs, "gross_profit": gross,
            "operating_expenses": expenses, "net_profit_before_income_tax": gross - expenses}


def account_balance(entries, code: str) -> Decimal:
    """Debit-positive balance."""
    return _net_by_account(entries).get(code, ZERO)
