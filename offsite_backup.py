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
    BACKUP_ENCRYPTION_KEY             voliteľné heslo (min. 20 znakov): zálohy sa pred nahratím
                                      zašifrujú (AES-256-GCM, viď backup_crypto.py) a v bucketi
                                      majú príponu .enc. Bez hesla sa nedajú obnoviť - ulož ho
                                      mimo Railway. Ak je heslo nastavené, ale nevyhovuje, nahrá
                                      sa NIČ (nikdy sa nenahrá nešifrovaná záloha omylom).

Zálohy obsahujú dáta zákazníkov - bucket musí byť SÚKROMNÝ (bez verejného prístupu).
Po nahratí sa záloha z bucketu znova STIAHNE, porovná sa jej SHA-256 s lokálnym
súborom a (ak je zadaný overovač) prejde úplným testom obnovy. Za funkčnú sa
nepovažuje, kým toto neprejde - samotné nahratie ani zhodná veľkosť nestačí.
Nahrávanie nikdy nezhodí vytváranie zálohy: chyba sa zapíše do stavu konta
a zobrazí v Nastaveniach.
"""

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import backup_crypto

logger = logging.getLogger(__name__)

REQUIRED = ("OFFSITE_BACKUP_BUCKET", "OFFSITE_BACKUP_ACCESS_KEY_ID", "OFFSITE_BACKUP_SECRET_ACCESS_KEY")


@dataclass
class OffsiteResult:
    ok: bool
    key: str | None = None
    error: str | None = None
    removed: int = 0
    sha256: str | None = None
    verified: bool = False
    warning: str | None = None
    encrypted: bool = False


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


def file_sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with open(path, "rb") as handle:

        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def upload_backup(
    slug: str,
    path: Path,
    validator: Callable[[Path], list[str]] | None = None,
) -> OffsiteResult:
    """
    Nahrá súbor zálohy, overí jeho veľkosť, potom ho z bucketu znova
    stiahne a porovná SHA-256 s lokálnym súborom. Ak je zadaný `validator`
    (vráti zoznam problémov), spustí ho nad stiahnutou kópiou (test
    obnovy). Staré zálohy konta poreže nad limit AŽ po úspešnom overení.
    Nikdy nevyhodí výnimku.
    """

    if not enabled():
        return OffsiteResult(ok=False, error="Nahrávanie mimo servera nie je nastavené.")

    secret = backup_crypto.passphrase()
    encrypted = secret is not None

    if encrypted:

        problems = backup_crypto.passphrase_problems(secret)

        if problems:
            return OffsiteResult(
                ok=False, encrypted=True,
                error="Šifrovanie záloh je zle nastavené, nič sa nenahralo: " + "; ".join(problems),
            )

    key = _prefix_for(slug) + path.name + (backup_crypto.SUFFIX if encrypted else "")
    downloaded: Path | None = None
    upload_source: Path = path
    encrypted_tmp: Path | None = None
    decrypted_tmp: Path | None = None

    try:

        client = _client()
        bucket = os.environ["OFFSITE_BACKUP_BUCKET"].strip()

        local_hash = file_sha256(path)

        if encrypted:

            fd, tmp_name = tempfile.mkstemp(suffix=".zip.enc")
            os.close(fd)
            encrypted_tmp = Path(tmp_name)
            backup_crypto.encrypt_file(path, encrypted_tmp, secret)
            upload_source = encrypted_tmp

        upload_hash = file_sha256(upload_source)

        client.upload_file(
            str(upload_source), bucket, key,
            ExtraArgs={"Metadata": {"sha256": upload_hash, "encrypted": "true" if encrypted else "false"}},
        )

        remote_size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]

        if remote_size != upload_source.stat().st_size:

            return OffsiteResult(
                ok=False, key=key, sha256=local_hash, encrypted=encrypted,
                error=f"Veľkosť v úložisku ({remote_size} B) sa nezhoduje s lokálnou ({upload_source.stat().st_size} B).",
            )

        # Obsah z bucketu sa stiahne späť a porovná - dve rovnako veľké
        # súbory ešte nemusia mať rovnaké dáta.
        fd, tmp_name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        downloaded = Path(tmp_name)

        client.download_file(bucket, key, str(downloaded))

        remote_hash = file_sha256(downloaded)

        if remote_hash != upload_hash:

            return OffsiteResult(
                ok=False, key=key, sha256=local_hash, encrypted=encrypted,
                error="Obsah zálohy v úložisku sa nezhoduje s lokálnym súborom (iný SHA-256).",
            )

        validated = downloaded

        if encrypted:

            # Test obnovy ide nad ROZŠIFROVANOU kópiou stiahnutou z bucketu:
            # overí sa, že heslo a šifrovanie naozaj dávajú pôvodnú zálohu.
            fd, tmp_name = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            decrypted_tmp = Path(tmp_name)

            try:
                backup_crypto.decrypt_file(downloaded, decrypted_tmp, secret)

            except backup_crypto.DecryptionError as exc:

                return OffsiteResult(
                    ok=False, key=key, sha256=local_hash, encrypted=True,
                    error=f"Zašifrovanú zálohu z úložiska sa nepodarilo rozšifrovať: {exc}"[:300],
                )

            if file_sha256(decrypted_tmp) != local_hash:

                return OffsiteResult(
                    ok=False, key=key, sha256=local_hash, encrypted=True,
                    error="Rozšifrovaná záloha sa nezhoduje s pôvodnou (iný SHA-256).",
                )

            validated = decrypted_tmp

        if validator is not None:

            problems = validator(validated)

            if problems:

                return OffsiteResult(
                    ok=False, key=key, sha256=local_hash, encrypted=encrypted,
                    error=("Záloha stiahnutá z úložiska neprešla testom obnovy: " + "; ".join(problems))[:300],
                )

        # Záloha je nahraná a overená. Čistenie starých záloh je až druhotné:
        # ak ho bucket zakáže (zámok objektov / verzie / chýbajúce právo na
        # mazanie - čo je pri ochrane pred zmazaním žiaduce), záloha ostáva
        # úspešná a len sa upozorní, že staré sa nemažú.
        removed = 0
        warning = None

        try:

            removed = _apply_retention(client, bucket, slug)

        except Exception as exc:   # noqa: BLE001

            logger.warning("Mazanie starých záloh v úložisku zlyhalo: %s", exc)
            warning = f"staré zálohy sa v úložisku nepodarilo zmazať ({type(exc).__name__})"

        logger.info("Záloha %s nahraná mimo servera a overená (%s), zmazané staré: %s", path.name, key, removed)

        return OffsiteResult(
            ok=True, key=key, removed=removed, sha256=local_hash, verified=True,
            warning=warning, encrypted=encrypted,
        )

    except Exception as exc:   # noqa: BLE001 - záloha nesmie zlyhať kvôli sieti

        logger.exception("Nahrávanie zálohy %s mimo servera zlyhalo", path.name)

        return OffsiteResult(ok=False, key=key, encrypted=encrypted, error=f"{type(exc).__name__}: {exc}"[:300])

    finally:

        for temp in (downloaded, encrypted_tmp, decrypted_tmp):

            if temp is not None:
                temp.unlink(missing_ok=True)


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
