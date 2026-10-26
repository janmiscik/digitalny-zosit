"""
Zakladanie a správa kont, ktoré robí MAJITEĽ SERVERA (admin stránka /admin/accounts a
`manage_accounts.py --create-account`). Jedna implementácia pre oba vstupy, aby
platili rovnaké pravidlá ako pri registrácii (dĺžka mena, silné heslo, unikátne meno).

Zámok `creation_lock` zdieľa aj verejná registrácia (routers/auth.py), takže súbežné
zakladanie kont nikdy nevyrobí dve konto s rovnakým menom.
"""

import os
import shutil
import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

import tenancy
from accounts_models import Account
from auth import hash_password, password_problem

creation_lock = threading.Lock()

MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 50
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


@dataclass
class Result:

    account: Account | None = None
    error: str | None = None


def server_admins() -> set[str]:
    """SERVER_ADMINS=meno1,meno2 - mená kont, ktoré smú spravovať kontá. Prázdne = nikto."""

    return {name.strip() for name in os.getenv("SERVER_ADMINS", "").split(",") if name.strip()}


def is_server_admin(username: str | None) -> bool:

    return bool(username) and username in server_admins()


def password_error(username: str, password: str) -> str | None:

    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Heslo musí mať aspoň {MIN_PASSWORD_LENGTH} znakov."

    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Heslo môže mať najviac {MAX_PASSWORD_LENGTH} znakov."

    return password_problem(password, username)


def create_account(accounts_db: Session, username: str, password: str) -> Result:

    username = (username or "").strip()

    if not (MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH):
        return Result(error=f"Meno musí mať {MIN_USERNAME_LENGTH} až {MAX_USERNAME_LENGTH} znakov.")

    problem = password_error(username, password)

    if problem:
        return Result(error=problem)

    with creation_lock:

        if accounts_db.query(Account).filter(Account.username == username).first() is not None:
            return Result(error="Toto používateľské meno je už obsadené.")

        def slug_exists(candidate: str) -> bool:
            return accounts_db.query(Account).filter(Account.slug == candidate).first() is not None

        slug = tenancy.ensure_unique_slug(tenancy.slugify_username(username), slug_exists)
        account = Account(username=username, slug=slug, password_hash=hash_password(password))

        accounts_db.add(account)
        accounts_db.commit()

        try:
            tenancy.provision_account(slug)

        except Exception:   # noqa: BLE001 - nedokončené konto sa vráti späť

            accounts_db.delete(account)
            accounts_db.commit()
            shutil.rmtree(tenancy.account_dir(slug), ignore_errors=True)

            return Result(error="Konto sa nepodarilo pripraviť, skús to prosím znova.")

    return Result(account=account)


def set_password(accounts_db: Session, slug: str, password: str) -> Result:
    """Nové heslo konta; všetky jeho doterajšie prihlásenia tým prestanú platiť."""

    account = accounts_db.query(Account).filter(Account.slug == slug).first()

    if account is None:
        return Result(error="Konto neexistuje.")

    problem = password_error(account.username, password)

    if problem:
        return Result(error=problem)

    account.password_hash = hash_password(password)
    accounts_db.commit()

    return Result(account=account)
