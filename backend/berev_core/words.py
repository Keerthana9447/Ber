"""Amount in words using the Indian numbering system (lakh / crore)."""
from decimal import Decimal, ROUND_HALF_UP

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        parts.append(_below_100(n))
    return " ".join(parts)


def number_to_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    if crore:
        parts.append(number_to_words(crore) + " Crore")
    if lakh:
        parts.append(_below_100(lakh) + " Lakh")
    if thousand:
        parts.append(_below_100(thousand) + " Thousand")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    if isinstance(amount, float):
        raise TypeError("use str/int/Decimal, not float")
    amt = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "Minus " if amt < 0 else ""
    amt = abs(amt)
    rupees = int(amt)
    paise = int((amt - rupees) * 100)
    text = f"Indian Rupees {number_to_words(rupees)}"
    if paise:
        text += f" and {_below_100(paise)} Paise"
    return f"{sign}{text} Only"
