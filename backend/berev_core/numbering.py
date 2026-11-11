"""Financial-year invoice numbering.

Numbers are allocated from a row in `number_sequences` using ONE atomic
UPSERT ... RETURNING statement, executed INSIDE the same DB transaction that
inserts the invoice:

  * concurrent issuers serialise on the sequence row lock -> no duplicates;
  * if the invoice transaction rolls back, the increment rolls back too
    -> no gaps (gapless series, which Indian invoicing expects).

GST Rule 46 limits the serial number to 16 characters made of letters,
digits, '-' and '/'. format_invoice_number enforces this (verify with your
tax professional). Default shape: INV/26-27/00001 (15 chars).
"""
from __future__ import annotations

import re
from datetime import date

MAX_LEN = 16
_ALLOWED = re.compile(r"^[A-Za-z0-9/-]+$")

UPSERT_SQL = {
    # sqlite3 (tests)
    "qmark": (
        "INSERT INTO number_sequences (series, last_number) VALUES (?, 1) "
        "ON CONFLICT (series) DO UPDATE SET last_number = number_sequences.last_number + 1 "
        "RETURNING last_number"
    ),
    # psycopg / psycopg2 (PostgreSQL)
    "format": (
        "INSERT INTO number_sequences (series, last_number) VALUES (%s, 1) "
        "ON CONFLICT (series) DO UPDATE SET last_number = number_sequences.last_number + 1 "
        "RETURNING last_number"
    ),
}


def financial_year_start(d: date, fy_start_month: int = 4) -> int:
    return d.year if d.month >= fy_start_month else d.year - 1


def fy_label(d: date, short: bool = True, fy_start_month: int = 4) -> str:
    s = financial_year_start(d, fy_start_month)
    e = s + 1
    return f"{s % 100:02d}-{e % 100:02d}" if short else f"{s}-{e % 100:02d}"


def series_key(doc_type: str, prefix: str, d: date, fy_start_month: int = 4) -> str:
    return f"{doc_type}:{prefix}:{financial_year_start(d, fy_start_month)}"


def format_invoice_number(prefix: str, d: date, seq: int, pad: int = 5,
                          fy_start_month: int = 4) -> str:
    if seq < 1:
        raise ValueError("sequence must be >= 1")
    number = f"{prefix}/{fy_label(d, True, fy_start_month)}/{seq:0{pad}d}"
    if not _ALLOWED.match(number):
        raise ValueError("invoice number may only contain letters, digits, '-' and '/'")
    if len(number) > MAX_LEN:
        raise ValueError(f"invoice number {number!r} is {len(number)} chars; max is {MAX_LEN}")
    return number


def next_sequence(cursor, series: str, paramstyle: str = "format") -> int:
    """Call inside the invoice-issuing transaction. Never commit between this
    call and inserting the invoice row."""
    cursor.execute(UPSERT_SQL[paramstyle], (series,))
    return cursor.fetchone()[0]
