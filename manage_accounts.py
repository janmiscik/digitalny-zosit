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
"""

import argparse
import sys

from accounts_db import AccountsSessionLocal
from accounts_models import Account
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

    args = parser.parse_args()

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
