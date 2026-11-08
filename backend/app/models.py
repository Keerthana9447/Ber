"""SQLAlchemy 2.x mappings generated from the canonical PostgreSQL schema."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, CHAR, Date, DateTime,
    ForeignKey, ForeignKeyConstraint, Identity, Index, Integer,
    Numeric, PrimaryKeyConstraint, SmallInteger, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

class State(Base):
    __tablename__ = "states"
    code: Mapped[str] = mapped_column(CHAR(2), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

class Unit(Base):
    __tablename__ = "units"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

class GstRate(Base):
    __tablename__ = "gst_rates"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    __table_args__ = (
        CheckConstraint(text('rate >= 0 AND rate <= 100')),
    )

class HsnSacCode(Base):
    __tablename__ = "hsn_sac_codes"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    default_gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5,2))
    default_cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    __table_args__ = (
        CheckConstraint(text("code ~ '^[0-9]{4,8}$'")),
        CheckConstraint(text("kind IN ('HSN','SAC')")),
    )

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ledger_kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    __table_args__ = (
        CheckConstraint(text("ledger_kind IN ('cash','bank')")),
    )

class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    state_code: Mapped[str] = mapped_column(CHAR(2), ForeignKey('states.code'), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))

class CompanySettings(Base):
    __tablename__ = "company_settings"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text('1'))
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    trade_name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    pan: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(CHAR(2), ForeignKey('states.code'))
    bank_name: Mapped[str | None] = mapped_column(Text)
    bank_account_no: Mapped[str | None] = mapped_column(Text)
    bank_ifsc: Mapped[str | None] = mapped_column(Text)
    upi_id: Mapped[str | None] = mapped_column(Text)
    logo_path: Mapped[str | None] = mapped_column(Text)
    invoice_prefix: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'INV'"))
    fy_start_month: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text('4'))
    allow_invoice_issue_without_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    terms_and_conditions: Mapped[str | None] = mapped_column(Text)
    authorized_signatory: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('id = 1')),
        CheckConstraint(text("invoice_prefix ~ '^[A-Za-z0-9/-]{1,6}$'")),
        CheckConstraint(text('fy_start_month BETWEEN 1 AND 12')),
    )

class NumberSequence(Base):
    __tablename__ = "number_sequences"
    series: Mapped[str] = mapped_column(Text, primary_key=True)
    last_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        CheckConstraint(text('last_number >= 0')),
    )

class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", back_populates="roles", lazy="selectin")

class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[Role]] = relationship(secondary="role_permissions", back_populates="permissions", lazy="selectin")

class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    permission_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False)
    __table_args__ = (
        PrimaryKeyConstraint('role_id', 'permission_id'),
    )

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('roles.id'), nullable=False)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    dealer_id: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    role: Mapped[Role] = relationship(lazy="joined")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class LoginAudit(Base):
    __tablename__ = "login_audit"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    email_attempted: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[Any | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    customer_type: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    pan: Mapped[str | None] = mapped_column(Text)
    billing_address: Mapped[str | None] = mapped_column(Text)
    shipping_address: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(CHAR(2), ForeignKey('states.code'))
    city: Mapped[str | None] = mapped_column(Text)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("customer_type IN ('individual','business','dealer','distributor','franchise','corporate')")),
        CheckConstraint(text('credit_limit >= 0')),
        CheckConstraint(text('payment_terms_days >= 0')),
    )

class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    pan: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(CHAR(2), ForeignKey('states.code'))
    city: Mapped[str | None] = mapped_column(Text)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class Dealer(Base):
    __tablename__ = "dealers"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    dealer_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('customers.id'), nullable=False, unique=True)
    territory: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("status IN ('pending','active','suspended','terminated')")),
    )

class ProductCategory(Base):
    __tablename__ = "product_categories"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_categories.id'))
    is_serialized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    code: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('product_categories.id'), nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    hsn_sac: Mapped[str] = mapped_column(Text, ForeignKey('hsn_sac_codes.code'), nullable=False)
    unit: Mapped[str] = mapped_column(Text, ForeignKey('units.code'), nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    selling_price: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    min_stock: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False, server_default=text('0'))
    is_serialized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    is_batch_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    is_service: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    image_path: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('purchase_price >= 0')),
        CheckConstraint(text('selling_price >= 0')),
        CheckConstraint(text('NOT (is_service AND is_serialized)')),
    )

class DealerPrice(Base):
    __tablename__ = "dealer_prices"
    dealer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('dealers.id'), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    __table_args__ = (
        CheckConstraint(text('price >= 0')),
        PrimaryKeyConstraint('dealer_id', 'product_id'),
    )

class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))

class Inventory(Base):
    __tablename__ = "inventory"
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('warehouses.id'), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False, server_default=text('0'))
    average_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14,4))
    __table_args__ = (
        CheckConstraint(text('quantity >= 0')),
        CheckConstraint(text('average_unit_cost >= 0')),
        PrimaryKeyConstraint('product_id', 'warehouse_id'),
    )

class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    batch_no: Mapped[str] = mapped_column(Text, nullable=False)
    mfg_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        UniqueConstraint('product_id', 'batch_no'),
    )

class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('suppliers.id'))
    supplier_invoice_no: Mapped[str | None] = mapped_column(Text)
    doc_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    place_of_supply_code: Mapped[str | None] = mapped_column(CHAR(2), ForeignKey('states.code'))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('purchases.id'))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("doc_type IN ('purchase_request','purchase_order','purchase_invoice')")),
        CheckConstraint(text("status IN ('draft','approved','ordered','received','posted','cancelled')")),
        UniqueConstraint('doc_type', 'number'),
    )

class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    purchase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('purchases.id', ondelete='CASCADE'), nullable=False)
    source_purchase_item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('purchase_items.id'))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hsn_sac: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    cgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        CheckConstraint(text('unit_price >= 0')),
    )

class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    grn_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    purchase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('purchases.id'), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('warehouses.id'), nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("status IN ('draft','posted','cancelled')")),
    )

class ProductSerial(Base):
    __tablename__ = "product_serials"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    chassis_no: Mapped[str | None] = mapped_column(Text)
    vin: Mapped[str | None] = mapped_column(Text)
    motor_no: Mapped[str | None] = mapped_column(Text)
    battery_serial: Mapped[str | None] = mapped_column(Text)
    battery_chemistry: Mapped[str | None] = mapped_column(Text)
    battery_capacity_kwh: Mapped[Decimal | None] = mapped_column(Numeric(8,2))
    variant: Mapped[str | None] = mapped_column(Text)
    mfg_date: Mapped[date | None] = mapped_column(Date)
    warranty_start: Mapped[date | None] = mapped_column(Date)
    warranty_months: Mapped[int | None] = mapped_column(Integer)
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('suppliers.id'))
    purchase_item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('purchase_items.id'))
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('warehouses.id'))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'available'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('warranty_months >= 0')),
        CheckConstraint(text("status IN ('available','reserved','sold','delivered','returned','under_service','transferred')")),
        CheckConstraint(text('chassis_no IS NOT NULL OR motor_no IS NOT NULL OR battery_serial IS NOT NULL OR vin IS NOT NULL')),
    )

class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    goods_receipt_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('goods_receipts.id', ondelete='CASCADE'), nullable=False)
    purchase_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('purchase_items.id'), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('batches.id'))
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        CheckConstraint(text('unit_cost >= 0')),
    )

class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('warehouses.id'), nullable=False)
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('batches.id'))
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_change: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14,4))
    ref_type: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("movement_type IN\n    ('purchase_in','sale_out','adjustment','transfer_in','transfer_out','purchase_return','sales_return','opening')")),
        CheckConstraint(text('quantity_change <> 0')),
    )

class Quotation(Base):
    __tablename__ = "quotations"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('customers.id'), nullable=False)
    quote_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    place_of_supply_code: Mapped[str] = mapped_column(CHAR(2), ForeignKey('states.code'), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    salesperson_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("status IN ('draft','sent','accepted','rejected','expired','converted')")),
    )

class QuotationItem(Base):
    __tablename__ = "quotation_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    quotation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('quotations.id', ondelete='CASCADE'), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hsn_sac: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
    )

class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('customers.id'), nullable=False)
    quotation_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('quotations.id'))
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    place_of_supply_code: Mapped[str] = mapped_column(CHAR(2), ForeignKey('states.code'), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    salesperson_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("status IN ('draft','confirmed','partially_delivered','delivered','invoiced','cancelled')")),
    )

class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('sales_orders.id', ondelete='CASCADE'), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hsn_sac: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
    )

class DeliveryChallan(Base):
    __tablename__ = "delivery_challans"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sales_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('sales_orders.id'))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('customers.id'), nullable=False)
    challan_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("status IN ('draft','dispatched','delivered','cancelled')")),
    )

class DeliveryChallanItem(Base):
    __tablename__ = "delivery_challan_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    challan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('delivery_challans.id', ondelete='CASCADE'), nullable=False)
    sales_order_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('sales_order_items.id'), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), nullable=False)
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        UniqueConstraint('challan_id', 'serial_id'),
    )

class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'tax_invoice'"))
    invoice_number: Mapped[str | None] = mapped_column(Text)
    issue_idempotency_key: Mapped[str | None] = mapped_column(Text)
    fy_start_year: Mapped[int | None] = mapped_column(Integer)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('customers.id'), nullable=False)
    seller_state_code: Mapped[str | None] = mapped_column(CHAR(2), ForeignKey('states.code'))
    sales_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('sales_orders.id'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    salesperson_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    seller_gstin: Mapped[str | None] = mapped_column(Text)
    customer_gstin: Mapped[str | None] = mapped_column(Text)
    billing_address: Mapped[str | None] = mapped_column(Text)
    shipping_address: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    seller_legal_name: Mapped[str | None] = mapped_column(Text)
    seller_trade_name: Mapped[str | None] = mapped_column(Text)
    seller_pan: Mapped[str | None] = mapped_column(Text)
    seller_address: Mapped[str | None] = mapped_column(Text)
    seller_logo_path: Mapped[str | None] = mapped_column(Text)
    seller_bank_name: Mapped[str | None] = mapped_column(Text)
    seller_bank_account_no: Mapped[str | None] = mapped_column(Text)
    seller_bank_ifsc: Mapped[str | None] = mapped_column(Text)
    seller_upi_id: Mapped[str | None] = mapped_column(Text)
    seller_terms_and_conditions: Mapped[str | None] = mapped_column(Text)
    seller_authorized_signatory: Mapped[str | None] = mapped_column(Text)
    hsn_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    place_of_supply_code: Mapped[str] = mapped_column(CHAR(2), ForeignKey('states.code'), nullable=False)
    is_inter_state: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reverse_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    payment_terms: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    issued_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("doc_type IN ('tax_invoice','bill_of_supply','proforma')")),
        CheckConstraint(text("status IN ('draft','approved','issued','cancelled')")),
        CheckConstraint(text('grand_total >= 0')),
        CheckConstraint(text("status = 'draft' OR status = 'approved' OR invoice_number IS NOT NULL OR doc_type = 'proforma'")),
        CheckConstraint(text('NOT is_inter_state OR (cgst_total = 0 AND sgst_total = 0)')),
        CheckConstraint(text('is_inter_state OR igst_total = 0')),
        CheckConstraint(text('grand_total = taxable_total + cgst_total + sgst_total + igst_total + cess_total + round_off')),
    )

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('invoices.id', ondelete='CASCADE'), nullable=False)
    product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('products.id'))
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    sales_order_item_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('sales_order_items.id'))
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('warehouses.id'))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_sac: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    cgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14,4))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        CheckConstraint(text('unit_price >= 0')),
    )

class CreditNote(Base):
    __tablename__ = "credit_notes"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    note_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('invoices.id'), nullable=False)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    restock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    hsn_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('grand_total > 0')),
        CheckConstraint(text("status IN ('draft','issued','cancelled')")),
    )

class CreditNoteItem(Base):
    __tablename__ = "credit_note_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    credit_note_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('credit_notes.id', ondelete='CASCADE'), nullable=False)
    invoice_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('invoice_items.id'), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_sac: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False)
    cess_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), nullable=False, server_default=text('0'))
    cgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('warehouses.id'))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14,4))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
    )

class DebitNote(Base):
    __tablename__ = "debit_notes"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    note_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    purchase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('purchases.id'), nullable=False)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    round_off: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'issued'"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('grand_total > 0')),
        CheckConstraint(text("status IN ('issued','cancelled')")),
    )

class DebitNoteItem(Base):
    __tablename__ = "debit_note_items"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    debit_note_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('debit_notes.id', ondelete='CASCADE'), nullable=False)
    purchase_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('purchase_items.id'), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    cgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    serial_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('product_serials.id'))
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('warehouses.id'))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        CheckConstraint(text('taxable_value >= 0')),
        CheckConstraint(text('unit_cost >= 0')),
    )

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    payment_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('customers.id'))
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('suppliers.id'))
    method_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('payment_methods.id'), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference_no: Mapped[str | None] = mapped_column(Text)
    is_advance: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'recorded'"))
    reverses_payment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('payments.id'))
    gateway_txn_id: Mapped[str | None] = mapped_column(Text, unique=True)
    gateway_signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    bank_reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    bank_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text("direction IN ('in','out')")),
        CheckConstraint(text('amount > 0')),
        CheckConstraint(text("status IN ('recorded','reversed')")),
        CheckConstraint(text('(customer_id IS NOT NULL)::int + (supplier_id IS NOT NULL)::int = 1')),
    )

class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('payments.id'), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('invoices.id'))
    purchase_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('purchases.id'))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('amount > 0')),
        CheckConstraint(text('(invoice_id IS NOT NULL)::int + (purchase_id IS NOT NULL)::int = 1')),
    )

class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    account_code: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))

class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    expense_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('expense_categories.id'), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    igst_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    cess_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    vendor: Mapped[str | None] = mapped_column(Text)
    method_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('payment_methods.id'))
    reference_no: Mapped[str | None] = mapped_column(Text)
    attachment_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    approved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('amount > 0')),
        CheckConstraint(text("status IN ('pending','approved','rejected','posted','cancelled')")),
    )

class LedgerAccount(Base):
    __tablename__ = "accounts"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('TRUE'))
    __table_args__ = (
        CheckConstraint(text("type IN ('asset','liability','equity','income','cogs','expense')")),
    )

class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    narration: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    reverses_entry_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('journal_entries.id'))
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('branches.id'))
    posted_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class JournalEntryLine(Base):
    __tablename__ = "journal_entry_lines"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('journal_entries.id'), nullable=False)
    account_code: Mapped[str] = mapped_column(Text, ForeignKey('accounts.code'), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    credit: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False, server_default=text('0'))
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('customers.id'))
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('suppliers.id'))
    __table_args__ = (
        CheckConstraint(text('(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)')),
    )

class InventoryOpeningValuation(Base):
    __tablename__ = "inventory_opening_valuations"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14,4), nullable=False)
    total_value: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)
    journal_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('journal_entries.id'), nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint(text('quantity > 0')),
        CheckConstraint(text('unit_cost >= 0')),
        CheckConstraint(text('total_value >= 0')),
        ForeignKeyConstraint(['product_id', 'warehouse_id'], ['inventory.product_id', 'inventory.warehouse_id']),
        UniqueConstraint('journal_entry_id', 'product_id', 'warehouse_id'),
    )

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    role_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('roles.id'))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[Any | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('now()'))

User.__table__.append_constraint(ForeignKeyConstraint(['dealer_id'], ['dealers.id'], name='users_dealer_fk'))
Index('users_email_uq', func.lower(User.email), unique=True)
Index('login_audit_user_ix', LoginAudit.user_id, LoginAudit.created_at.desc(), unique=False)
Index('customers_name_ix', func.lower(Customer.name), unique=False)
Index('customers_phone_ix', Customer.phone, unique=False)
Index('customers_gstin_uq', Customer.gstin, unique=True, postgresql_where=text('gstin IS NOT NULL AND deleted_at IS NULL'))
Index('suppliers_gstin_uq', Supplier.gstin, unique=True, postgresql_where=text('gstin IS NOT NULL AND deleted_at IS NULL'))
Index('products_name_ix', func.lower(Product.name), unique=False)
Index('purchases_supplier_inv_uq', Purchase.supplier_id, Purchase.supplier_invoice_no, unique=True, postgresql_where=text("doc_type = 'purchase_invoice' AND supplier_invoice_no IS NOT NULL AND status <> 'cancelled'"))
Index('purchase_items_source_ix', PurchaseItem.source_purchase_item_id, unique=False, postgresql_where=text('source_purchase_item_id IS NOT NULL'))
Index('serial_chassis_uq', func.upper(ProductSerial.chassis_no), unique=True, postgresql_where=text('chassis_no IS NOT NULL'))
Index('serial_vin_uq', func.upper(ProductSerial.vin), unique=True, postgresql_where=text('vin IS NOT NULL'))
Index('serial_motor_uq', func.upper(ProductSerial.motor_no), unique=True, postgresql_where=text('motor_no IS NOT NULL'))
Index('serial_battery_uq', func.upper(ProductSerial.battery_serial), unique=True, postgresql_where=text('battery_serial IS NOT NULL'))
Index('serial_status_ix', ProductSerial.product_id, ProductSerial.status, unique=False)
Index('goods_receipt_items_serial_uq', GoodsReceiptItem.serial_id, unique=True, postgresql_where=text('serial_id IS NOT NULL'))
Index('stock_mov_prod_ix', StockMovement.product_id, StockMovement.created_at.desc(), unique=False)
Index('stock_mov_ref_ix', StockMovement.ref_type, StockMovement.ref_id, unique=False)
Index('delivery_challan_items_order_ix', DeliveryChallanItem.sales_order_item_id, unique=False)
Index('invoices_number_uq', Invoice.invoice_number, unique=True, postgresql_where=text('invoice_number IS NOT NULL'))
Index('invoices_issue_idempotency_uq', Invoice.issue_idempotency_key, unique=True, postgresql_where=text('issue_idempotency_key IS NOT NULL'))
Index('invoices_cust_ix', Invoice.customer_id, Invoice.invoice_date.desc(), unique=False)
Index('invoices_date_ix', Invoice.invoice_date, unique=False)
Index('invoices_status_ix', Invoice.status, unique=False)
Index('invoice_items_inv_ix', InvoiceItem.invoice_id, unique=False)
Index('invoice_items_order_item_ix', InvoiceItem.sales_order_item_id, unique=False, postgresql_where=text('sales_order_item_id IS NOT NULL'))
Index('invoice_items_inv_serial_uq', InvoiceItem.invoice_id, InvoiceItem.serial_id, unique=True, postgresql_where=text('serial_id IS NOT NULL'))
Index('debit_note_items_note_ix', DebitNoteItem.debit_note_id, unique=False)
Index('payments_cust_ix', Payment.customer_id, Payment.payment_date, unique=False)
Index('payments_supp_ix', Payment.supplier_id, Payment.payment_date, unique=False)
Index('alloc_invoice_ix', PaymentAllocation.invoice_id, unique=False)
Index('alloc_purchase_ix', PaymentAllocation.purchase_id, unique=False)
Index('je_date_ix', JournalEntry.entry_date, unique=False)
Index('je_source_ix', JournalEntry.source_type, JournalEntry.source_id, unique=False)
Index('jel_acct_ix', JournalEntryLine.account_code, unique=False)
Index('jel_entry_ix', JournalEntryLine.entry_id, unique=False)
Index('inventory_opening_valuation_time_ix', InventoryOpeningValuation.created_at.desc(), unique=False)
Index('notif_user_ix', Notification.user_id, Notification.is_read, Notification.created_at.desc(), unique=False)
Index('audit_entity_ix', AuditLog.entity_type, AuditLog.entity_id, unique=False)
Index('audit_time_ix', AuditLog.created_at.desc(), unique=False)
Index('payment_reversal_uq', Payment.reverses_payment_id, unique=True, postgresql_where=text('reverses_payment_id IS NOT NULL'))
