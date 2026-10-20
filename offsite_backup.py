"""
Voliteľné nahrávanie záloh MIMO Railway (S3-kompatibilné úložisko).

Zálohy v /app/data/accounts/<konto>/backups ležia na tom istom Volume ako
dáta - pri jeho strate zmiznú spolu s nimi. Ak sú nastavené premenné nižšie,
každá úspešne vytvorená a overená záloha sa nahrá aj do externého bucketu
(Backblaze B2, Cloudflare R2, AWS S3, MinIO...). Bez nich je funkcia vypnutá.

    OFFSITE_BACKUP_BUCKET             názov bucketu (povinné)
    OFFSITE_BACKUP_ACCESS_KEY_ID      prístupový kľúč (povinné)
    OFFSITE_BACKUP_SECRET_ACCESS_KEY  tajný kľúč (povinné)
    OFFSITE_BACKUP_ENDPOINT           URL S3 API (napr. https://s3.eu-central-003.backblazeb2.com);
                                      pri AWS S3 sa nechá prázdne
    OFFSITE_BACKUP_REGION             región (predvolené "auto")
    OFFSITE_BACKUP_PREFIX             predpona kľúčov (predvolené "digitalny-zosit")
    OFFSITE_BACKUP_KEEP               koľko záloh na konto držať v bucketi (predvolené 30)

Zálohy obsahujú dáta zákazníkov - bucket musí byť SÚKROMNÝ (bez verejného prístupu).
Nahrávanie nikdy nezhodí vytváranie zálohy: chyba sa zapíše do stavu konta
a zobrazí v Nastaveniach.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED = ("OFFSITE_BACKUP_BUCKET", "OFFSITE_BACKUP_ACCESS_KEY_ID", "OFFSITE_BACKUP_SECRET_ACCESS_KEY")


@dataclass
class OffsiteResult:
    ok: bool
    key: str | None = None
    error: str | None = None
    removed: int = 0


def enabled() -> bool:

    return all(os.getenv(name, "").strip() for name in REQUIRED)


def _keep() -> int:

    try:
        return max(1, int(os.getenv("OFFSITE_BACKUP_KEEP", "")))

    except ValueError:
        return 30


def _prefix_for(slug: str) -> str:

    prefix = os.getenv("OFFSITE_BACKUP_PREFIX", "digitalny-zosit").strip().strip("/") or "digitalny-zosit"

    return f"{prefix}/{slug}/"


def _client():
    """S3 klient (v testoch sa nahrádza atrapou)."""

    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.getenv("OFFSITE_BACKUP_ENDPOINT", "").strip() or None,
        region_name=os.getenv("OFFSITE_BACKUP_REGION", "auto").strip() or "auto",
        aws_access_key_id=os.environ["OFFSITE_BACKUP_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["OFFSITE_BACKUP_SECRET_ACCESS_KEY"].strip(),
        config=Config(
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
        ),
    )


def upload_backup(slug: str, path: Path) -> OffsiteResult:
    """
    Nahrá súbor zálohy, overí jeho veľkosť v bucketi a poreže staré zálohy
    konta nad limit. Nikdy nevyhodí výnimku.
    """

    if not enabled():
        return OffsiteResult(ok=False, error="Nahrávanie mimo servera nie je nastavené.")

    key = _prefix_for(slug) + path.name

    try:

        client = _client()
        bucket = os.environ["OFFSITE_BACKUP_BUCKET"].strip()

        client.upload_file(str(path), bucket, key)

        remote_size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]

        if remote_size != path.stat().st_size:

            return OffsiteResult(
                ok=False, key=key,
                error=f"Veľkosť v úložisku ({remote_size} B) sa nezhoduje s lokálnou ({path.stat().st_size} B).",
            )

        removed = _apply_retention(client, bucket, slug)

        logger.info("Záloha %s nahraná mimo servera (%s), zmazané staré: %s", path.name, key, removed)

        return OffsiteResult(ok=True, key=key, removed=removed)

    except Exception as exc:   # noqa: BLE001 - záloha nesmie zlyhať kvôli sieti

        logger.exception("Nahrávanie zálohy %s mimo servera zlyhalo", path.name)

        return OffsiteResult(ok=False, key=key, error=f"{type(exc).__name__}: {exc}"[:300])


def _apply_retention(client, bucket: str, slug: str) -> int:

    objects = []
    token = None

    while True:

        kwargs = {"Bucket": bucket, "Prefix": _prefix_for(slug)}

        if token:
            kwargs["ContinuationToken"] = token

        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))

        if not page.get("IsTruncated"):
            break

        token = page.get("NextContinuationToken")

    objects.sort(key=lambda item: item["LastModified"], reverse=True)

    removed = 0

    for item in objects[_keep():]:

        client.delete_object(Bucket=bucket, Key=item["Key"])
        removed += 1

    return removed
