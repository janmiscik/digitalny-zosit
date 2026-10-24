"""
Rozšifruje zálohu z bucketu (súbor .zip.enc) na obyčajné .zip, ktoré sa dá
nahrať cez Nastavenia -> Obnova zo zálohy.

    python decrypt_backup.py zaloha.zip.enc zaloha.zip

Heslo sa berie z premennej BACKUP_ENCRYPTION_KEY, inak sa opýta (nezobrazuje sa).
"""

import getpass
import os
import sys
from pathlib import Path

from backup_crypto import DecryptionError, decrypt_file


def main(argv: list[str]) -> int:

    if len(argv) != 3:
        print(__doc__)
        return 2

    source, target = Path(argv[1]), Path(argv[2])

    if not source.is_file():
        print(f"Súbor neexistuje: {source}")
        return 2

    if target.exists():
        print(f"Cieľ už existuje, nechcem ho prepísať: {target}")
        return 2

    secret = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip() or getpass.getpass("Heslo (BACKUP_ENCRYPTION_KEY): ")

    try:
        decrypt_file(source, target, secret)

    except DecryptionError as exc:
        print(f"CHYBA: {exc}")
        return 1

    print(f"Hotovo: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
