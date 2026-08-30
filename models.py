from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, Date, Numeric
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


    # =====================================
    # PEPPOL (fáza 2 - príprava na e-fakturáciu)
    # Identifikačná schéma odberateľa v Peppol sieti - JE INÁ hodnota
    # než Company.peppol_scheme_id (to je schéma dodávateľa). Kým appka
    # nezbiera tento údaj aktívne, ostáva prázdna a appka jednoducho
    # nevygeneruje EndpointID pre odberateľa (radšej nič, než nesprávny
    # identifikátor - viď peppol_xml.py).
    # =====================================

    peppol_scheme_id = Column(
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

    website = Column(
        String,
        nullable=True
    )

    swift_bic = Column(
        String,
        nullable=True
    )

    # =====================================
    # DPH REŽIM
    # Väčšina drobných remeselníkov/živnostníkov začína ako neplatca DPH.
    # Tento prepínač riadi, či appka vôbec smie na faktúrach účtovať DPH
    # (viď invoice_utils.validate_vat_regime) - nie je odvodený len z
    # toho, či je vyplnené IČ DPH, aby mal používateľ plnú kontrolu.
    # =====================================

    is_vat_payer = Column(
        Boolean,
        nullable=False,
        default=False
    )

    # =====================================
    # PEPPOL (fáza 2 - príprava na e-fakturáciu)
    # Presný kód schémy ti pridelí/potvrdí tvoj poskytovateľ
    # (Digitálny poštár) pri registrácii na Peppol sieť.
    # =====================================

    peppol_scheme_id = Column(
        String,
        nullable=True
    )

    # =====================================
    # LOGO A PODPIS/PEČIATKA (na faktúre)
    # Uchovávame len názov súboru, samotný súbor je v priečinku uploads/
    # =====================================

    logo_filename = Column(
        String,
        nullable=True
    )

    signature_filename = Column(
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

    # Konštantný symbol - štandardná hodnota pre úhradu tovaru/služieb je
    # 0308, appka ju defaultne predvyplní, ale dá sa zmeniť/vymazať.
    constant_symbol = Column(
        String,
        nullable=True
    )

    specific_symbol = Column(
        String,
        nullable=True
    )

    payment_method = Column(
        String,
        nullable=False,
        default="Prevodom"
    )

    # =====================================
    # PRENESENIE DAŇOVEJ POVINNOSTI (tuzemské samozdanenie, §69 ods.12)
    # Platí len medzi dvoma platiteľmi DPH (dodávateľ aj odberateľ musia
    # mať IČ DPH) - viď invoice_utils.validate_reverse_charge_eligibility.
    # Ak True, faktúra sa vystavuje BEZ DPH a musí obsahovať povinný text
    # "Prenesenie daňovej povinnosti" (§74 ods.1 písm. k zákona o DPH).
    # =====================================

    reverse_charge = Column(
        Boolean,
        nullable=False,
        default=False
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
