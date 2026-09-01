from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =========================================
# JOB STATUS
# =========================================

class JobStatus(str, Enum):

    NEW = "Nová"
    AGREED = "Dohodnutá"
    IN_PROGRESS = "Prebieha"
    WAITING_MATERIAL = "Čaká na materiál"
    DONE = "Hotová"


# =========================================
# INVOICE STATUS
# =========================================

class InvoiceStatus(str, Enum):

    DRAFT = "Návrh"
    SENT = "Odoslaná"
    PAID = "Uhradená"
    OVERDUE = "Po splatnosti"
    CANCELLED = "Stornovaná"


# =========================================
# STAV CENOVEJ PONUKY
# =========================================

class QuoteStatus(str, Enum):

    DRAFT = "Návrh"
    SENT = "Odoslaná"
    ACCEPTED = "Akceptovaná"
    REJECTED = "Zamietnutá"
    CONVERTED = "Prevedená na faktúru"
    EXPIRED = "Po platnosti"


# =========================================
# SLOVENSKÉ SADZBY DPH (platné od 1.1.2025)
# =========================================

VAT_RATES = (0, 5, 19, 23)


# =========================================
# CUSTOMER
# =========================================

class CustomerBase(BaseModel):

    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    note: str | None = None

    ico: str | None = None
    dic: str | None = None
    ic_dph: str | None = None


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(CustomerBase):
    pass


class CustomerRead(CustomerBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# JOB
# =========================================

class JobBase(BaseModel):

    title: str
    description: str | None = None
    status: JobStatus = JobStatus.NEW
    due_date: date | None = None


class JobCreate(JobBase):

    customer_id: int


class JobUpdate(JobBase):
    pass


class JobRead(JobBase):

    id: int
    customer_id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# COMPANY (fakturačné údaje predávajúceho)
# =========================================

class CompanyBase(BaseModel):

    name: str
    ico: str | None = None
    dic: str | None = None
    ic_dph: str | None = None
    address: str | None = None
    city: str | None = None
    zip_code: str | None = None
    iban: str | None = None
    swift_bic: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    peppol_scheme_id: str | None = None
    logo_filename: str | None = None
    signature_filename: str | None = None


class CompanyUpdate(CompanyBase):
    pass


class CompanyRead(CompanyBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# INVOICE ITEM
# =========================================

class InvoiceItemBase(BaseModel):

    description: str
    quantity: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        max_digits=10,
        decimal_places=2
    )
    unit: str = "ks"
    unit_price: Decimal = Field(
        ge=0,
        max_digits=10,
        decimal_places=2
    )
    vat_rate: int = 23

    @field_validator("vat_rate")
    @classmethod
    def check_vat_rate(cls, value: int) -> int:

        if value not in VAT_RATES:

            raise ValueError(
                f"Neplatná sadzba DPH: {value}. "
                f"Povolené sú: {', '.join(str(r) for r in VAT_RATES)}"
            )

        return value


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemRead(InvoiceItemBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# QUOTE ITEM (rovnaká validácia ako InvoiceItem)
# =========================================

class QuoteItemBase(BaseModel):

    description: str
    quantity: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        max_digits=10,
        decimal_places=2
    )
    unit: str = "ks"
    unit_price: Decimal = Field(
        ge=0,
        max_digits=10,
        decimal_places=2
    )
    vat_rate: int = 23

    @field_validator("vat_rate")
    @classmethod
    def check_vat_rate(cls, value: int) -> int:

        if value not in VAT_RATES:

            raise ValueError(
                f"Neplatná sadzba DPH: {value}. "
                f"Povolené sú: {', '.join(str(r) for r in VAT_RATES)}"
            )

        return value


class QuoteItemCreate(QuoteItemBase):
    pass


class QuoteItemRead(QuoteItemBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# INVOICE
# =========================================

class InvoiceBase(BaseModel):

    customer_id: int
    job_id: int | None = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issue_date: date
    due_date: date
    delivery_date: date | None = None
    variable_symbol: str | None = None
    payment_method: str = "Prevodom"
    note: str | None = None


class InvoiceCreate(InvoiceBase):

    items: list[InvoiceItemCreate] = Field(
        default_factory=list,
        min_length=1
    )


class InvoiceUpdate(InvoiceBase):

    items: list[InvoiceItemCreate] = Field(
        default_factory=list,
        min_length=1
    )


class InvoiceRead(InvoiceBase):

    id: int
    invoice_number: str
    items: list[InvoiceItemRead]

    model_config = ConfigDict(
        from_attributes=True
    )
