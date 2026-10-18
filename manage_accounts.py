"""
Údržbový CLI skript pre multi-tenant appku.

Pri KAŽDOM nasadení appky s novou migráciou business schémy (alembic/
versions/) treba migráciu spustiť na databáze VŠETKÝCH existujúcich
kont, nie len na novo registrovaných - tie novú migráciu dostanú
automaticky pri registrácii (tenancy.provision_account), existujúce
kontá nie.

Použitie:

    python manage_accounts.py --upgrade-all
    python manage_accounts.py --list
    python manage_accounts.py --backup-all [--kind pre-upgrade] [--only-if-migration-pending]
    python manage_accounts.py --verify-all
    python manage_accounts.py --list-orphans
    python manage_accounts.py --reattach SLUG --username MENO [--password HESLO]
"""

import argparse
import getpass
import sys

from accounts_db import AccountsSessionLocal
from accounts_models import Account
import auto_backup
import tenancy
from auth import hash_password


def list_accounts() -> list[Account]:

    db = AccountsSessionLocal()

    try:
        return db.query(Account).order_by(Account.id).all()

    finally:
        db.close()


def upgrade_all() -> None:

    accounts = list_accounts()

    if not accounts:

        print("Žiadne registrované kontá.")
        return

    print(f"Spúšťam business migrácie na {len(accounts)} kontách...")

    failures = []

    for account in accounts:

        print(f"  - {account.username} ({account.slug})...", end=" ")

        try:

            tenancy.upgrade_account_database(account.slug)
            print("OK")

        except Exception as exc:

            print(f"CHYBA: {exc}")
            failures.append((account, exc))

    if failures:

        print(
            f"\n{len(failures)} z {len(accounts)} kont sa nepodarilo "
            "zmigrovať:"
        )

        for account, exc in failures:
            print(f"  - {account.username} ({account.slug}): {exc}")

        sys.exit(1)

    print("\nVšetky kontá úspešne zmigrované.")


def backup_all(kind: str, only_if_migration_pending: bool) -> None:
    """
    Vytvorí a overí zálohu všetkých kont. S `only_if_migration_pending`
    sa zálohujú len kontá, ktorým čaká migrácia schémy (poistka pred
    `--upgrade-all`: ak by migrácia pokazila dáta, je z čoho obnoviť).
    """

    accounts = list_accounts()

    if not accounts:

        print("Žiadne registrované kontá.")
        return

    failures = []
    processed = 0

    for account in accounts:

        if only_if_migration_pending and not tenancy.account_needs_migration(account.slug):
            continue

        processed += 1

        print(f"  - {account.username} ({account.slug})...", end=" ")

        result = auto_backup.run_backup(account.slug, kind)

        if result.ok:
            print(f"OK ({result.file})")

        else:
            print(f"CHYBA: {result.error}")
            failures.append(account)

    if processed == 0:

        print("Žiadne kontá nepotrebujú poistnú zálohu (žiadna čakajúca migrácia).")
        return

    if failures:

        print(f"\n{len(failures)} z {processed} záloh sa nepodarilo vytvoriť.")
        sys.exit(1)

    print("\nVšetky zálohy vytvorené a overené.")


def verify_all() -> None:
    """Test obnovy nad najnovšou zálohou každého konta."""

    accounts = list_accounts()

    if not accounts:

        print("Žiadne registrované kontá.")
        return

    failures = []

    for account in accounts:

        print(f"  - {account.username} ({account.slug})...", end=" ")

        result = auto_backup.verify_existing_backup(account.slug)

        if result.ok:
            print(f"OK ({result.file})")

        else:
            print(f"CHYBA: {result.error}")
            failures.append(account)

    if failures:
        sys.exit(1)


def find_orphan_slugs() -> list[str]:
    """Priečinky kont na disku, ku ktorým neexistuje konto v accounts.db
    (typicky po strate/prepísaní accounts.db - dáta sú v poriadku, len
    chýba záznam s menom a heslom)."""

    root = tenancy.ACCOUNTS_DATA_DIR

    if not root.exists():
        return []

    known = {account.slug for account in list_accounts()}

    return sorted(
        item.name for item in root.iterdir()
        if item.is_dir() and item.name not in known
        and (item / "database.db").exists()
    )


def list_orphans() -> None:

    orphans = find_orphan_slugs()

    if not orphans:

        print("Žiadne osirelé dáta - každý priečinok kont má svoje konto.")
        return

    print("Dáta bez konta (dajú sa pripojiť cez --reattach):")

    for slug in orphans:
        print(f"  - {slug}")


def reattach(slug: str, username: str, password: str | None) -> None:
    """
    Vytvorí konto `username` nad EXISTUJÚCIMI dátami v priečinku `slug`
    (nič sa nekopíruje ani nemaže). Používa sa na obnovu prístupu, keď
    sa stratila/prepísala accounts.db, ale priečinok konta ostal.
    """

    if slug not in find_orphan_slugs():

        print(f"Priečinok '{slug}' neexistuje alebo už má konto. Skús --list-orphans.")
        sys.exit(1)

    username = username.strip()

    if not username:

        print("Zadaj meno cez --username.")
        sys.exit(1)

    if password is None:

        password = getpass.getpass("Nové heslo: ")

        if password != getpass.getpass("Heslo znova: "):

            print("Heslá sa nezhodujú.")
            sys.exit(1)

    if len(password) < 8:

        print("Heslo musí mať aspoň 8 znakov.")
        sys.exit(1)

    db = AccountsSessionLocal()

    try:

        if db.query(Account).filter(Account.username == username).first():

            print(f"Meno '{username}' už existuje.")
            sys.exit(1)

        db.add(Account(username=username, slug=slug, password_hash=hash_password(password)))
        db.commit()

    finally:
        db.close()

    # doplní prípadné novšie migrácie schémy nad starými dátami
    tenancy.upgrade_account_database(slug)

    print(f"Hotovo: prihlás sa ako '{username}' - uvidíš pôvodné dáta z '{slug}'.")


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--upgrade-all",
        action="store_true",
        help="Spustí business migrácie (alembic/) na všetkých kontách."
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="Vypíše zoznam registrovaných kont."
    )

    parser.add_argument(
        "--backup-all",
        action="store_true",
        help="Vytvorí a overí zálohu všetkých kont."
    )

    parser.add_argument(
        "--kind",
        choices=["auto", "manual", "pre-upgrade"],
        default="manual",
        help="Druh zálohy pri --backup-all (predvolene manual)."
    )

    parser.add_argument(
        "--only-if-migration-pending",
        action="store_true",
        help="Pri --backup-all zálohuje len kontá s čakajúcou migráciou."
    )

    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Test obnovy nad najnovšou zálohou každého konta."
    )

    parser.add_argument("--list-orphans", action="store_true",
                        help="Vypíše dáta kont, ktoré nemajú záznam v accounts.db.")

    parser.add_argument("--reattach", metavar="SLUG",
                        help="Pripojí osirelé dáta (priečinok SLUG) k novému kontu.")

    parser.add_argument("--username", help="Meno konta pri --reattach.")

    parser.add_argument("--password", help="Heslo pri --reattach (inak sa opýta).")

    args = parser.parse_args()

    if args.list_orphans:

        list_orphans()
        return

    if args.reattach:

        reattach(args.reattach, args.username or "", args.password)
        return

    if args.backup_all:

        backup_all(args.kind, args.only_if_migration_pending)
        return

    if args.verify_all:

        verify_all()
        return

    if args.upgrade_all:

        upgrade_all()
        return

    if args.list:

        accounts = list_accounts()

        if not accounts:
            print("Žiadne registrované kontá.")
            return

        for account in accounts:
            print(f"{account.id}\t{account.username}\t{account.slug}\t{account.created_at}")

        return

    parser.print_help()


if __name__ == "__main__":
    main()
