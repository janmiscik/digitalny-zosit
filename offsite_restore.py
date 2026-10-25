"""
Stiahnutie záloh z bucketu mimo servera (R2/S3/B2) na počítač - pre prípad,
že Railway (alebo iný hosting) nie je dostupný a treba appku spustiť inde.

Používa tie isté premenné ako nahrávanie (OFFSITE_BACKUP_*), a ak sú zálohy
zašifrované, aj BACKUP_ENCRYPTION_KEY (alebo sa opýta na heslo).

    python offsite_restore.py --list                      zoznam záloh všetkých kont
    python offsite_restore.py --list --account admin      zoznam záloh jedného konta
    python offsite_restore.py --download --account admin  stiahne NAJNOVŠIU zálohu konta
    python offsite_restore.py --download --account admin --name rucne-2026-10-24.zip.enc
    python offsite_restore.py --verify                    skúška obnovy: najnovšia záloha KAŻDÉHO konta
                                                          sa stiahne, rozšifruje a overí (nič sa nenahrá)
    voliteľne: --out priečinok (predvolene ./obnova)

Výsledné .zip sa potom nahrá v appke: Nastavenia -> Obnoviť zo zálohy
(po registrácii konta s rovnakým menom). Súbory sa nikdy neprepisujú.
"""

import argparse
import getpass
import os
import re
import sys
import tempfile
from pathlib import Path

import backup_crypto
import offsite_backup

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _list_objects(client, bucket: str, prefix: str) -> list[dict]:

    objects, token = [], None

    while True:

        kwargs = {"Bucket": bucket, "Prefix": prefix}

        if token:
            kwargs["ContinuationToken"] = token

        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))

        if not page.get("IsTruncated"):
            return objects

        token = page.get("NextContinuationToken")


def _root_prefix() -> str:

    prefix = os.getenv("OFFSITE_BACKUP_PREFIX", "digitalny-zosit").strip().strip("/") or "digitalny-zosit"

    return prefix + "/"


def list_backups(client, bucket: str, account: str | None = None) -> list[dict]:
    """Zálohy v bucketi: [{account, name, key, size, modified}], najnovšie prvé."""

    prefix = offsite_backup._prefix_for(account) if account else _root_prefix()
    root = _root_prefix()
    rows = []

    for item in _list_objects(client, bucket, prefix):

        relative = item["Key"][len(root):]
        parts = relative.split("/")

        if len(parts) != 2 or not parts[1]:
            continue

        rows.append({
            "account": parts[0], "name": parts[1], "key": item["Key"],
            "size": item.get("Size"), "modified": item["LastModified"],
        })

    rows.sort(key=lambda row: row["modified"], reverse=True)

    return rows


def download_backup(client, bucket: str, row: dict, out_dir: Path, secret: str | None) -> Path:
    """Stiahne zálohu; `.enc` rozšifruje na .zip. Existujúci súbor neprepíše."""

    if not SAFE_NAME.match(row["name"]):
        raise ValueError(f"Nebezpečný názov súboru v bucketi: {row['name']!r}")

    out_dir.mkdir(parents=True, exist_ok=True)

    encrypted = row["name"].endswith(backup_crypto.SUFFIX)
    final = out_dir / f"{row['account']}-{row['name'][:-len(backup_crypto.SUFFIX)] if encrypted else row['name']}"

    if final.exists():
        raise FileExistsError(f"Súbor už existuje, neprepisujem: {final}")

    if not encrypted:

        client.download_file(bucket, row["key"], str(final))

        return final

    if not secret:
        raise ValueError("Záloha je zašifrovaná - nastav BACKUP_ENCRYPTION_KEY alebo zadaj heslo.")

    fd, tmp_name = tempfile.mkstemp(suffix=".enc")
    os.close(fd)
    tmp = Path(tmp_name)

    try:

        client.download_file(bucket, row["key"], str(tmp))
        backup_crypto.decrypt_file(tmp, final, secret)

    finally:

        tmp.unlink(missing_ok=True)

    return final


def verify_latest(client, bucket: str, secret: str | None, account: str | None = None) -> list[dict]:
    """
    Skúška obnovy: pre každé konto stiahne NAJNOVŠIU zálohu do dočasného
    priečinka, rozšifruje ju a spustí rovnaký test obnovy ako appka
    (integrita DB, počty riadkov, prílohy). Dočasné súbory zmaže.
    """

    import auto_backup    # ťažký import - až keď je naozaj treba

    latest: dict[str, dict] = {}

    for row in list_backups(client, bucket, account):    # zoradené od najnovšej
        latest.setdefault(row["account"], row)

    results = []

    for name, row in sorted(latest.items()):

        with tempfile.TemporaryDirectory() as tmp:

            try:
                path = download_backup(client, bucket, row, Path(tmp), secret)
                problems = auto_backup.verify_backup_zip(path).problems

            except (ValueError, FileExistsError, backup_crypto.DecryptionError, OSError) as exc:
                problems = [str(exc)[:200]]

        results.append({"account": name, "name": row["name"], "modified": row["modified"], "problems": list(problems)})

    return results


def main(argv: list[str]) -> int:

    parser = argparse.ArgumentParser(description="Stiahnutie záloh z bucketu mimo servera")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--account")
    parser.add_argument("--name")
    parser.add_argument("--out", default="obnova")
    args = parser.parse_args(argv[1:])

    if [args.list, args.download, args.verify].count(True) != 1:
        parser.error("Zvoľ presne jedno z --list, --download alebo --verify.")

    if not offsite_backup.enabled():
        print("CHYBA: nastav OFFSITE_BACKUP_BUCKET, OFFSITE_BACKUP_ACCESS_KEY_ID a OFFSITE_BACKUP_SECRET_ACCESS_KEY.")
        return 2

    client = offsite_backup._client()
    bucket = os.environ["OFFSITE_BACKUP_BUCKET"].strip()

    if args.verify:

        secret = backup_crypto.passphrase()
        results = verify_latest(client, bucket, secret, args.account)

        if not results:
            print("CHYBA: v bucketi nie je žiadna záloha.")
            return 1

        for result in results:
            state = "OK " if not result["problems"] else "CHYBA"
            print(f"{state} {result['account']:20} {result['name']} ({result['modified']:%Y-%m-%d %H:%M})")

            for problem in result["problems"]:
                print(f"      - {problem}")

        failed = sum(1 for result in results if result["problems"])
        print(f"Overených kont: {len(results)}, s chybou: {failed}")
        return 1 if failed else 0

    rows = list_backups(client, bucket, args.account)

    if args.list:

        for row in rows:
            print(f"{row['account']:20} {row['name']:50} {row['modified']:%Y-%m-%d %H:%M} {row['size'] or '?'} B")

        print(f"Spolu: {len(rows)}")
        return 0

    if not args.account:
        parser.error("--download vyžaduje --account.")

    if args.name:
        rows = [row for row in rows if row["name"] == args.name]

    if not rows:
        print("CHYBA: žiadna zodpovedajúca záloha v bucketi.")
        return 1

    secret = backup_crypto.passphrase()

    if rows[0]["name"].endswith(backup_crypto.SUFFIX) and not secret:
        secret = getpass.getpass("Heslo (BACKUP_ENCRYPTION_KEY): ")

    try:
        path = download_backup(client, bucket, rows[0], Path(args.out), secret)

    except (ValueError, FileExistsError, backup_crypto.DecryptionError) as exc:
        print(f"CHYBA: {exc}")
        return 1

    print(f"Hotovo: {path}")
    print("Nahraj ho v appke: Nastavenia -> Obnoviť zo zálohy (po registrácii konta s rovnakým menom).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
