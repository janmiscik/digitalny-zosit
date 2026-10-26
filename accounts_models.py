from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, true

from accounts_db import AccountsBase


class Account(AccountsBase):
    """
    Jedno prihlasovacie konto. NEOBSAHUJE žiadne business dáta appky
    (firma, faktúry, zákazníci...) - tie žijú vo vlastnej databáze
    tohto konta, v súbore pomenovanom podľa `slug` (viď tenancy.py).

    `username` aj `slug` sú samostatné polia zámerne: `username` je to,
    čo si používateľ zadáva pri prihlásení a vie si ho kedykoľvek
    zmeniť bez toho, aby sa menilo umiestnenie jeho dát na disku -
    `slug` sa priradí raz pri registrácii a už sa nemení.
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)

    username = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    # Priečinok na disku pre dáta tohto konta (data/accounts/<slug>/).
    # Prideľuje sa raz pri registrácii a NEMENÍ sa, ani keď si
    # používateľ zmení username - inak by appka musela pri zmene mena
    # presúvať celý priečinok s dátami.
    slug = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = Column(
        String,
        nullable=False
    )

    # Voliteľný e-mail na obnovu zabudnutého hesla (nastavuje si ho
    # používateľ v Nastaveniach; bez neho sa heslo cez e-mail obnoviť nedá).
    email = Column(
        String,
        nullable=True
    )

    # Vypnuté konto sa nevie prihlásiť (existujúce prihlásenia prestanú platiť),
    # ale jeho dáta aj zálohy ostávajú. Vypína a zapína majiteľ servera.
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true()
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
