"""
Diagnostika trvalého úložiska - spúšťa sa pri každom štarte (start.sh)
a vypíše do logu, či sú dáta na pripojenom disku (Railway Volume).

Nič nemení. Výpis hľadaj v Railway -> Deployments -> View logs na
riadkoch začínajúcich "[ULOZISKE]".
"""

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path("/app/data") if Path("/app").exists() else Path("./data")


def mount_for(path: Path) -> str | None:
    """Vráti riadok z /proc/mounts, ak je `path` samostatný mount (Volume)."""

    try:

        for line in Path("/proc/mounts").read_text().splitlines():

            parts = line.split()

            if len(parts) >= 3 and parts[1] == str(path):
                return f"{parts[0]} ({parts[2]})"

    except OSError:
        pass

    return None


def say(message: str) -> None:
    print(f"[ULOZISKE] {message}", flush=True)


def main() -> None:

    say(f"ACCOUNTS_DATABASE_URL = {os.getenv('ACCOUNTS_DATABASE_URL')}")
    say(f"ACCOUNTS_DATA_DIR     = {os.getenv('ACCOUNTS_DATA_DIR')}")

    mount = mount_for(DATA_DIR)

    if mount:
        say(f"OK: {DATA_DIR} je samostatný disk (Volume): {mount}")

    else:
        say(
            f"VAROVANIE: {DATA_DIR} NIE JE samostatný disk. Dáta sa stratia pri "
            "každom novom nasadení! Pripoj Volume s Mount path /app/data "
            "(Railway: pravý klik na službu -> Attach volume)."
        )

    db_file = DATA_DIR / "accounts.db"

    if not db_file.exists():
        say(f"accounts.db zatiaľ neexistuje ({db_file}) - vznikne novou migráciou.")
        return

    try:

        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        conn.close()
        say(f"accounts.db: {db_file.stat().st_size} B, registrovaných kont: {count}")

    except sqlite3.Error as exc:
        say(f"accounts.db sa nepodarilo prečítať: {exc}")

    accounts_dir = DATA_DIR / "accounts"

    if accounts_dir.exists():

        names = sorted(item.name for item in accounts_dir.iterdir() if item.is_dir())
        say(f"priečinky kont: {names or '-'}")


if __name__ == "__main__":
    main()
