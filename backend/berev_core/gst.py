"""GST calculation engine (pure Python, stdlib only, Decimal arithmetic).

ASSUMPTIONS (review with a qualified Indian tax professional before use):
 1. Unit prices are EXCLUSIVE of GST. Tax-inclusive pricing is not supported.
 2. Supply is intra-state when seller state code == place-of-supply state code,
    otherwise inter-state. Exports, SEZ supplies, deemed exports and import
    of services are NOT modelled.
 3. Tax is computed per line on the taxable value and rounded half-up to
    paise. For intra-state lines CGST is computed at rate/2 and SGST is set
    equal to CGST, so CGST == SGST always.
 4. Invoice grand total is rounded to the nearest rupee (half-up); the
    difference is shown as round-off.
 5. Allowed GST rates are CONFIGURATION. DEFAULT_ALLOWED_RATES is only a
    permissive superset; the application must load the currently notified
    rates from the administrator-managed master, per HSN/SAC.
 6. Money inputs must be int, str or Decimal. Floats are rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Optional, Sequence

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")

DEFAULT_ALLOWED_RATES = frozenset(
    Decimal(x) for x in ("0", "0.25", "3", "5", "12", "18", "28", "40")
)

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory", "99": "Centre Jurisdiction",
}


class GSTError(ValueError):
    """Raised for any invalid GST/invoice input."""


def to_decimal(value, field: str = "value") -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise GSTError(f"{field}: use int, str or Decimal, not {type(value).__name__}")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise GSTError(f"{field}: not a number: {value!r}") from None
    if not d.is_finite():
        raise GSTError(f"{field}: must be finite")
    return d


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- GSTIN
_GSTIN_RE = re.compile(r"^(\d{2})[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _CHARS[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str, expected_state_code: Optional[str] = None) -> bool:
    """Format + checksum validation. Does NOT prove the GSTIN is registered/active."""
    g = (gstin or "").strip().upper()
    m = _GSTIN_RE.match(g)
    if not m or m.group(1) not in STATE_CODES:
        return False
    if expected_state_code and m.group(1) != expected_state_code:
        return False
    return gstin_checksum_char(g[:14]) == g[14]


# ---------------------------------------------------------------- lines
@dataclass(frozen=True)
class LineInput:
    description: str
    hsn_sac: str
    quantity: object
    unit_price: object          # exclusive of GST
    gst_rate: object            # percent, e.g. "18"
    discount_pct: object = 0    # percent of gross, OR
    discount_amount: object = 0 # absolute amount (not both)
    cess_rate: object = 0       # percent
    unit: str = "NOS"


@dataclass(frozen=True)
class LineResult:
    description: str
    hsn_sac: str
    unit: str
    quantity: Decimal
    unit_price: Decimal
    gross: Decimal
    discount: Decimal
    taxable: Decimal
    gst_rate: Decimal
    cess_rate: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal

    @property
    def tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst + self.cess

    @property
    def total(self) -> Decimal:
        return self.taxable + self.tax


def compute_line(line: LineInput, inter_state: bool, tax_applicable: bool = True,
                 allowed_rates: Iterable[Decimal] = DEFAULT_ALLOWED_RATES) -> LineResult:
    if not line.description or not line.description.strip():
        raise GSTError("description is required")
    if not re.fullmatch(r"\d{4,8}", str(line.hsn_sac or "")):
        raise GSTError(f"{line.description}: HSN/SAC must be 4-8 digits")

    qty = to_decimal(line.quantity, "quantity")
    price = to_decimal(line.unit_price, "unit_price")
    pct = to_decimal(line.discount_pct, "discount_pct")
    amt = to_decimal(line.discount_amount, "discount_amount")
    rate = to_decimal(line.gst_rate, "gst_rate")
    cess_rate = to_decimal(line.cess_rate, "cess_rate")

    if qty <= 0:
        raise GSTError("quantity must be > 0")
    if qty.as_tuple().exponent < -3:
        raise GSTError("quantity allows at most 3 decimal places")
    if price < 0:
        raise GSTError("unit_price cannot be negative")
    if pct and amt:
        raise GSTError("give discount_pct or discount_amount, not both")
    if not (ZERO <= pct <= HUNDRED):
        raise GSTError("discount_pct must be between 0 and 100")
    if amt < 0:
        raise GSTError("discount_amount cannot be negative")

    gross = q2(qty * price)
    discount = q2(gross * pct / HUNDRED) if pct else q2(amt)
    if discount > gross:
        raise GSTError("discount exceeds line amount")
    taxable = gross - discount

    cgst = sgst = igst = cess = ZERO
    if tax_applicable:
        if rate not in set(allowed_rates):
            raise GSTError(f"GST rate {rate}% is not in the configured rates")
        if cess_rate < 0:
            raise GSTError("cess_rate cannot be negative")
        if inter_state:
            igst = q2(taxable * rate / HUNDRED)
        else:
            cgst = q2(taxable * (rate / 2) / HUNDRED)
            sgst = cgst
        cess = q2(taxable * cess_rate / HUNDRED)
    else:
        rate = cess_rate = ZERO

    return LineResult(line.description, str(line.hsn_sac), line.unit, qty, price,
                      gross, discount, taxable, rate, cess_rate, cgst, sgst, igst, cess)


# ---------------------------------------------------------------- invoice
@dataclass(frozen=True)
class HsnSummaryRow:
    hsn_sac: str
    gst_rate: Decimal
    cess_rate: Decimal
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal


@dataclass(frozen=True)
class InvoiceResult:
    lines: tuple
    is_inter_state: bool
    tax_applicable: bool
    taxable_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    cess_total: Decimal
    tax_total: Decimal
    total_before_round: Decimal
    round_off: Decimal
    grand_total: Decimal
    hsn_summary: tuple


def is_inter_state(seller_state_code: str, place_of_supply_code: str) -> bool:
    for label, code in (("seller", seller_state_code), ("place of supply", place_of_supply_code)):
        if code not in STATE_CODES:
            raise GSTError(f"unknown {label} state code: {code!r}")
    return seller_state_code != place_of_supply_code


def compute_invoice(lines: Sequence[LineInput], seller_state_code: str,
                    place_of_supply_code: str, tax_applicable: bool = True,
                    allowed_rates: Iterable[Decimal] = DEFAULT_ALLOWED_RATES,
                    round_to_rupee: bool = True) -> InvoiceResult:
    """tax_applicable=False is for a Bill of Supply (no tax shown/charged)."""
    if not lines:
        raise GSTError("an invoice needs at least one line")
    inter = is_inter_state(seller_state_code, place_of_supply_code)
    allowed = frozenset(allowed_rates)
    results = tuple(compute_line(l, inter, tax_applicable, allowed) for l in lines)

    taxable = sum((r.taxable for r in results), ZERO)
    cgst = sum((r.cgst for r in results), ZERO)
    sgst = sum((r.sgst for r in results), ZERO)
    igst = sum((r.igst for r in results), ZERO)
    cess = sum((r.cess for r in results), ZERO)
    tax_total = cgst + sgst + igst + cess
    before = taxable + tax_total
    grand = before.quantize(Decimal("1"), rounding=ROUND_HALF_UP) if round_to_rupee else before
    round_off = grand - before

    groups: dict = {}
    for r in results:
        k = (r.hsn_sac, r.gst_rate, r.cess_rate)
        g = groups.setdefault(k, [ZERO] * 5)
        for i, v in enumerate((r.taxable, r.cgst, r.sgst, r.igst, r.cess)):
            g[i] += v
    summary = tuple(HsnSummaryRow(k[0], k[1], k[2], *v) for k, v in sorted(groups.items()))

    return InvoiceResult(results, inter, tax_applicable, taxable, cgst, sgst, igst, cess,
                         tax_total, before, round_off, grand, summary)
