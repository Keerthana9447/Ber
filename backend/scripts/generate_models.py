from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "backend" / "db" / "schema.sql"
OUTPUT = ROOT / "backend" / "app" / "models.py"

CLASS_NAMES = {
    "states": "State", "units": "Unit", "gst_rates": "GstRate",
    "hsn_sac_codes": "HsnSacCode", "payment_methods": "PaymentMethod",
    "branches": "Branch", "company_settings": "CompanySettings",
    "number_sequences": "NumberSequence", "roles": "Role",
    "permissions": "Permission", "role_permissions": "RolePermission",
    "users": "User", "refresh_tokens": "RefreshToken",
    "password_reset_tokens": "PasswordResetToken", "login_audit": "LoginAudit",
    "customers": "Customer", "suppliers": "Supplier", "dealers": "Dealer",
    "product_categories": "ProductCategory", "products": "Product",
    "dealer_prices": "DealerPrice", "warehouses": "Warehouse",
    "inventory": "Inventory", "batches": "Batch", "purchases": "Purchase",
    "purchase_items": "PurchaseItem", "goods_receipts": "GoodsReceipt",
    "goods_receipt_items": "GoodsReceiptItem", "product_serials": "ProductSerial",
    "stock_movements": "StockMovement", "quotations": "Quotation",
    "quotation_items": "QuotationItem", "sales_orders": "SalesOrder",
    "sales_order_items": "SalesOrderItem", "delivery_challans": "DeliveryChallan",
    "delivery_challan_items": "DeliveryChallanItem",
    "invoices": "Invoice", "invoice_items": "InvoiceItem",
    "credit_notes": "CreditNote", "credit_note_items": "CreditNoteItem",
    "debit_notes": "DebitNote", "debit_note_items": "DebitNoteItem", "payments": "Payment",
    "payment_allocations": "PaymentAllocation",
    "inventory_opening_valuations": "InventoryOpeningValuation",
    "expense_categories": "ExpenseCategory", "expenses": "Expense",
    "accounts": "LedgerAccount", "journal_entries": "JournalEntry",
    "journal_entry_lines": "JournalEntryLine", "notifications": "Notification",
    "audit_logs": "AuditLog",
}

TYPE_MAP = {
    "CHAR": ("CHAR", "str"), "TEXT": ("Text", "str"),
    "BIGINT": ("BigInteger", "int"), "INT": ("Integer", "int"),
    "INTEGER": ("Integer", "int"), "SMALLINT": ("SmallInteger", "int"),
    "NUMERIC": ("Numeric", "Decimal"), "BOOLEAN": ("Boolean", "bool"),
    "DATE": ("Date", "date"), "TIMESTAMPTZ": ("DateTime(timezone=True)", "datetime"),
    "INET": ("INET", "Any"), "JSONB": ("JSONB", "dict[str, Any]"),
}
CONSTRAINT_START = re.compile(r"^(?:CONSTRAINT\b|PRIMARY\s+KEY\b|UNIQUE\b|CHECK\b|FOREIGN\s+KEY\b)", re.I)
TABLE_START = re.compile(r"CREATE\s+TABLE\s+(\w+)\s*\((.*?)\);", re.I | re.S)


def split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = depth = 0
    quoted = False
    i = 0
    while i < len(value):
        char = value[i]
        if char == "'":
            if quoted and i + 1 < len(value) and value[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(value[start:i].strip())
                start = i + 1
        i += 1
    parts.append(value[start:].strip())
    return parts


def parenthesized(value: str, opening: int) -> tuple[str, int]:
    depth = 0
    quoted = False
    for i in range(opening, len(value)):
        char = value[i]
        if char == "'":
            if quoted and i + 1 < len(value) and value[i + 1] == "'":
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return value[opening + 1:i], i + 1
    raise ValueError(f"unclosed SQL parenthesis in {value!r}")


def sql_string(value: str) -> str:
    return repr(value)


def column_definition(segment: str) -> tuple[str, str, list[str]]:
    match = re.match(r"(\w+)\s+([A-Z]+)(\([^)]*\))?(.*)$", segment.strip(), re.I | re.S)
    if not match:
        raise ValueError(f"unsupported column definition: {segment}")
    name, sql_type, params, rest = match.groups()
    sql_type = sql_type.upper()
    if sql_type not in TYPE_MAP:
        raise ValueError(f"unsupported SQL type {sql_type} on {name}")
    sqlalchemy_type, python_type = TYPE_MAP[sql_type]
    if params:
        sqlalchemy_type += params

    args = [sqlalchemy_type]
    if "GENERATED ALWAYS AS IDENTITY" in rest.upper():
        args.append("Identity(always=True)")
    reference = re.search(r"\bREFERENCES\s+(\w+)\s*\((\w+)\)", rest, re.I)
    if reference:
        on_delete = re.search(r"\bON\s+DELETE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)", rest, re.I)
        delete_arg = f", ondelete={sql_string(on_delete.group(1).upper())}" if on_delete else ""
        args.append(f"ForeignKey({sql_string(reference.group(1) + '.' + reference.group(2))}{delete_arg})")
    primary = bool(re.search(r"\bPRIMARY\s+KEY\b", rest, re.I))
    unique = bool(re.search(r"\bUNIQUE\b", rest, re.I))
    if primary:
        args.append("primary_key=True")
    elif re.search(r"\bNOT\s+NULL\b", rest, re.I):
        args.append("nullable=False")
    if unique:
        args.append("unique=True")
    default = re.search(
        r"\bDEFAULT\s+('(?:[^']|'')*'|[A-Za-z_][\w]*(?:\([^)]*\))?|[-+]?\d+(?:\.\d+)?)",
        rest, re.I,
    )
    if default:
        args.append(f"server_default=text({sql_string(default.group(1))})")
    annotation = python_type if primary or re.search(r"\bNOT\s+NULL\b", rest, re.I) else f"{python_type} | None"
    code = (
        f"    {name}: Mapped[{annotation}] = mapped_column("
        f"{', '.join(args)})"
    )

    checks: list[str] = []
    cursor = 0
    while True:
        check = re.search(r"\bCHECK\s*\(", rest[cursor:], re.I)
        if not check:
            break
        opening = cursor + check.end() - 1
        expression, cursor = parenthesized(rest, opening)
        checks.append(expression)
    return code, name, checks


def build() -> str:
    sql = re.sub(r"--[^\r\n]*", "", SCHEMA.read_text(encoding="utf-8"))
    tables: list[tuple[str, list[str], list[str]]] = []
    for match in TABLE_START.finditer(sql):
        table_name = match.group(1)
        if table_name not in CLASS_NAMES:
            raise ValueError(f"missing class mapping for table {table_name}")
        columns: list[str] = []
        constraints: list[str] = []
        for segment in split_top_level(match.group(2)):
            if not segment:
                continue
            if CONSTRAINT_START.match(segment):
                named = re.match(r"CONSTRAINT\s+(\w+)\s+(.*)$", segment, re.I | re.S)
                body = named.group(2) if named else segment
                name_arg = f", name={sql_string(named.group(1))}" if named else ""
                if re.match(r"PRIMARY\s+KEY", body, re.I):
                    inner, _ = parenthesized(body, body.index("("))
                    constraints.append(f"PrimaryKeyConstraint({', '.join(sql_string(c.strip()) for c in split_top_level(inner))}{name_arg})")
                elif re.match(r"UNIQUE", body, re.I):
                    inner, _ = parenthesized(body, body.index("("))
                    constraints.append(f"UniqueConstraint({', '.join(sql_string(c.strip()) for c in split_top_level(inner))}{name_arg})")
                elif re.match(r"CHECK", body, re.I):
                    inner, _ = parenthesized(body, body.index("("))
                    constraints.append(f"CheckConstraint(text({sql_string(inner)}){name_arg})")
                elif re.match(r"FOREIGN\s+KEY", body, re.I):
                    local, pos = parenthesized(body, body.index("("))
                    target = re.search(r"\bREFERENCES\s+(\w+)\s*\(", body[pos:], re.I)
                    if not target:
                        raise ValueError(f"unsupported table foreign key: {segment}")
                    target_start = pos + target.end() - 1
                    remote, _ = parenthesized(body, target_start)
                    constraints.append(
                        f"ForeignKeyConstraint([{', '.join(sql_string(c.strip()) for c in split_top_level(local))}], "
                        f"[{', '.join(sql_string(target.group(1) + '.' + c.strip()) for c in split_top_level(remote))}]{name_arg})"
                    )
                else:
                    raise ValueError(f"unsupported table constraint: {segment}")
                continue
            code, _, checks = column_definition(segment)
            columns.append(code)
            constraints.extend(
                f"CheckConstraint(text({sql_string(expression)}))"
                for expression in checks
            )
        tables.append((table_name, columns, constraints))

    if len(tables) != len(CLASS_NAMES):
        raise ValueError(f"expected {len(CLASS_NAMES)} tables, parsed {len(tables)}")

    lines = [
        '"""SQLAlchemy 2.x mappings generated from the canonical PostgreSQL schema."""',
        "from __future__ import annotations",
        "",
        "from datetime import date, datetime",
        "from decimal import Decimal",
        "from typing import Any",
        "",
        "from sqlalchemy import (",
        "    BigInteger, Boolean, CheckConstraint, CHAR, Date, DateTime,",
        "    ForeignKey, ForeignKeyConstraint, Identity, Index, Integer,",
        "    Numeric, PrimaryKeyConstraint, SmallInteger, Text, UniqueConstraint,",
        "    func, text,",
        ")",
        "from sqlalchemy.dialects.postgresql import INET, JSONB",
        "from sqlalchemy.orm import Mapped, mapped_column, relationship",
        "",
        "from app.db.base import Base",
        "",
    ]
    for table_name, columns, constraints in tables:
        class_name = CLASS_NAMES[table_name]
        lines += [f"class {class_name}(Base):", f'    __tablename__ = "{table_name}"']
        lines.extend(columns)
        if table_name == "roles":
            lines.append('    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", back_populates="roles", lazy="selectin")')
        elif table_name == "permissions":
            lines.append('    roles: Mapped[list[Role]] = relationship(secondary="role_permissions", back_populates="permissions", lazy="selectin")')
        elif table_name == "users":
            lines.append('    role: Mapped[Role] = relationship(lazy="joined")')
        if constraints:
            lines.append("    __table_args__ = (")
            lines.extend(f"        {constraint}," for constraint in constraints)
            lines.append("    )")
        lines.append("")

    alters = re.findall(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+FOREIGN\s+KEY\s*\(([^)]+)\)"
        r"\s+REFERENCES\s+(\w+)\s*\(([^)]+)\)",
        sql, re.I,
    )
    for table, name, local, target, remote in alters:
        model = CLASS_NAMES[table]
        local_cols = ", ".join(sql_string(col.strip()) for col in local.split(","))
        remote_cols = ", ".join(sql_string(target + "." + col.strip()) for col in remote.split(","))
        lines.append(
            f"{model}.__table__.append_constraint(ForeignKeyConstraint("
            f"[{local_cols}], [{remote_cols}], name={sql_string(name)}))"
        )

    for unique, name, table, expressions, where in re.findall(
        r"CREATE\s+(UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(([^;]+?)\)"
        r"(?:\s+WHERE\s+([^;]+))?;",
        sql, re.I | re.S,
    ):
        model = CLASS_NAMES[table]
        expressions = expressions.strip()
        columns_expr: list[str] = []
        for expression in split_top_level(expressions):
            expr = expression.strip()
            desc = re.fullmatch(r"(\w+)\s+DESC", expr, re.I)
            function = re.fullmatch(r"(\w+)\s*\(\s*(\w+)\s*\)", expr)
            if desc:
                columns_expr.append(f"{model}.{desc.group(1)}.desc()")
            elif function:
                function_name = function.group(1).lower()
                if function_name not in {"lower", "upper"}:
                    raise ValueError(f"unsupported index expression {expr}")
                columns_expr.append(f"func.{function_name}({model}.{function.group(2)})")
            elif re.fullmatch(r"\w+", expr):
                columns_expr.append(f"{model}.{expr}")
            else:
                raise ValueError(f"unsupported index expression {expr}")
        args = ", ".join(columns_expr)
        options = [f"unique={bool(unique)}"]
        if where:
            options.append(f"postgresql_where=text({sql_string(where.strip())})")
        lines.append(f"Index({sql_string(name)}, {args}, {', '.join(options)})")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    generated = build()
    if "--check" in sys.argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != generated:
            print(f"{OUTPUT} is out of date; run python scripts/generate_models.py")
            return 1
        print("ORM mappings are in sync with schema.sql.")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8", newline="\n")
    print(f"Generated {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
