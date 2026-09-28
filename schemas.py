import re
from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from validators import (
    validate_dic_format,
    validate_email_format,
    validate_ic_dph_format,
    validate_ico_format,
    validate_iban_format,
    validate_peppol_scheme_id,
)


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
    city: str | None = None
    zip_code: str | None = None
    note: str | None = None

    ico: str | None = None
    dic: str | None = None
    ic_dph: str | None = None

    # Peppol (fáza 2) - krajina a identifikačná schéma odberateľa v
    # Peppol sieti. Obe nepovinné - appka bez nich Peppol export
    # jednoducho nepridá EndpointID/presnú krajinu, len na to
    # informatívne upozorní (viď peppol_validation.py).
    country_code: str | None = "SK"
    peppol_scheme_id: str | None = None

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str | None) -> str | None:

        if not value:
            return value

        return validate_email_format(value)

    @field_validator("ico")
    @classmethod
    def check_ico(cls, value: str | None) -> str | None:

        if not value:
            return value

        return validate_ico_format(value)

    @field_validator("dic")
    @classmethod
    def check_dic(cls, value: str | None) -> str | None:

        if not value:
            return value

        return validate_dic_format(value)

    @field_validator("ic_dph")
    @classmethod
    def check_ic_dph(cls, value: str | None) -> str | None:

        if not value:
            return value

        return validate_ic_dph_format(value)

    @field_validator("country_code")
    @classmethod
    def check_country_code(cls, value: str | None) -> str | None:

        if not value:
            return value

        stripped = value.strip().upper()

        if not re.match(r"^[A-Z]{2}$", stripped):

            raise ValueError(
                f"Kód krajiny '{value}' by mal mať presne 2 písmená "
                "(ISO 3166-1 alpha-2), napr. SK, CZ, DE."
            )

        return stripped

    @field_validator("peppol_scheme_id")
    @classmethod
    def check_peppol_scheme_id(cls, value: str | None) -> str | None:

        if not value:
            return value

        # Krajina sa tu (na úrovni jedného poľa) ešte nedá skrížiť s
        # country_code - Pydantic field_validator vidí len jednu
        # hodnotu naraz, format-only kontrola je zámerne v
        # validators.py. Kríženie (schéma vs. krajina odberateľa)
        # rieši model_validator nižšie, kde sú obe hodnoty k
        # dispozícii súčasne.
        return validate_peppol_scheme_id(value)

    @model_validator(mode="after")
    def check_peppol_scheme_matches_country(self) -> "CustomerBase":

        if self.peppol_scheme_id and self.country_code:

            try:
                validate_peppol_scheme_id(
                    self.peppol_scheme_id,
                    expected_country_code=self.country_code
                )

            except ValueError as exc:

                raise ValueError(str(exc))

        return self


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

    # Appka počíta vždy s jednou (slovenskou) firmou ako predávajúcim
    # (viď models.Company docstring), preto sa schéma tu vždy overuje
    # oproti "SK" - na rozdiel od CustomerBase, kde krajina odberateľa
    # je premenlivá.
    @field_validator("peppol_scheme_id")
    @classmethod
    def check_peppol_scheme_id(cls, value: str | None) -> str | None:

        if not value:
            return value

        return validate_peppol_scheme_id(value, expected_country_code="SK")


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
