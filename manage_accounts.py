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
"""

import argparse
import sys

from accounts_db import AccountsSessionLocal
from accounts_models import Account
import auto_backup
import tenancy


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

    args = parser.parse_args()

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
