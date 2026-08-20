from sqlalchemy import Column, Integer, String, Text, ForeignKey, Date, Numeric
from sqlalchemy.orm import relationship

from database import Base


class Customer(Base):

    __tablename__ = "customers"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    name = Column(
        String,
        nullable=False
    )


    phone = Column(
        String,
        nullable=True
    )


    email = Column(
        String,
        nullable=True
    )


    address = Column(
        String,
        nullable=True
    )


    note = Column(
        Text,
        nullable=True
    )


    # =====================================
    # FAKTURAČNÉ ÚDAJE (pre firmy/živnostníkov ako odberateľov)
    # Pri fyzických osobách nechaj prázdne.
    # =====================================

    ico = Column(
        String,
        nullable=True
    )

    dic = Column(
        String,
        nullable=True
    )

    ic_dph = Column(
        String,
        nullable=True
    )


    jobs = relationship(
        "Job",
        back_populates="customer",
        cascade="all, delete-orphan"
    )

    invoices = relationship(
        "Invoice",
        back_populates="customer",
        cascade="all, delete-orphan"
    )


class Job(Base):

    __tablename__ = "jobs"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    title = Column(
        String,
        nullable=False
    )


    description = Column(
        Text,
        nullable=True
    )


    status = Column(
        String,
        nullable=False,
        default="Nová"
    )


    due_date = Column(
        Date,
        nullable=True
    )


    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )


    customer = relationship(
        "Customer",
        back_populates="jobs"
    )

    invoices = relationship(
        "Invoice",
        back_populates="job"
    )


class Company(Base):
    """
    Údaje o firme/živnosti používateľa appky (predávajúci na faktúrach).
    Očakáva sa jeden riadok (nastavenia appky).
    """

    __tablename__ = "company"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    name = Column(
        String,
        nullable=False
    )

    ico = Column(
        String,
        nullable=True
    )

    dic = Column(
        String,
        nullable=True
    )

    ic_dph = Column(
        String,
        nullable=True
    )

    address = Column(
        String,
        nullable=True
    )

    city = Column(
        String,
        nullable=True
    )

    zip_code = Column(
        String,
        nullable=True
    )

    iban = Column(
        String,
        nullable=True
    )

    email = Column(
        String,
        nullable=True
    )

    phone = Column(
        String,
        nullable=True
    )


class Invoice(Base):

    __tablename__ = "invoices"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    invoice_number = Column(
        String,
        nullable=False,
        unique=True,
        index=True
    )

    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    job_id = Column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=True
    )

    status = Column(
        String,
        nullable=False,
        default="Návrh"
    )

    issue_date = Column(
        Date,
        nullable=False
    )

    due_date = Column(
        Date,
        nullable=False
    )

    delivery_date = Column(
        Date,
        nullable=True
    )

    variable_symbol = Column(
        String,
        nullable=True
    )

    note = Column(
        Text,
        nullable=True
    )


    customer = relationship(
        "Customer",
        back_populates="invoices"
    )

    job = relationship(
        "Job",
        back_populates="invoices"
    )

    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan"
    )


class InvoiceItem(Base):

    __tablename__ = "invoice_items"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    invoice_id = Column(
        Integer,
        ForeignKey("invoices.id"),
        nullable=False
    )

    description = Column(
        String,
        nullable=False
    )

    quantity = Column(
        Numeric(10, 2),
        nullable=False,
        default=1
    )

    unit = Column(
        String,
        nullable=False,
        default="ks"
    )

    unit_price = Column(
        Numeric(10, 2),
        nullable=False
    )

    vat_rate = Column(
        Integer,
        nullable=False,
        default=23
    )


    invoice = relationship(
        "Invoice",
        back_populates="items"
    )
