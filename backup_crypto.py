"""
Šifrovanie záloh pred nahratím mimo servera.

Ak je nastavená premenná BACKUP_ENCRYPTION_KEY (heslo, min. 20 znakov), každá
záloha sa pred nahratím do bucketu zašifruje (AES-256-GCM po blokoch, kľúč sa
odvodí cez scrypt zo hesla a náhodnej soli) a v bucketi má príponu `.enc`.
Poskytovateľ úložiska tak nevidí faktúry ani zákazníkov.

POZOR: heslo si ULOŽ MIMO Railway (správca hesiel). Bez neho sa zašifrované
zálohy nedajú obnoviť - nikto ich nevie rozšifrovať, ani my.

Formát súboru:
    "DZENC1" | soľ (16) | prefix nonce (8) | veľkosť bloku (4, big-endian)
    potom bloky: AES-GCM(ciphertext + tag 16 B), nonce = prefix + počítadlo (4 B),
    AAD = hlavička + počítadlo + príznak posledného bloku (ochrana pred
    odrezaním, zámenou a preusporiadaním blokov).

Rozšifrovanie: python decrypt_backup.py vstup.zip.enc vystup.zip
"""

import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"DZENC1"
SALT_LEN = 16
NONCE_PREFIX_LEN = 8
TAG_LEN = 16
DEFAULT_CHUNK = 1024 * 1024
MIN_PASSPHRASE_LENGTH = 20
SUFFIX = ".enc"
HEADER_LEN = len(MAGIC) + SALT_LEN + NONCE_PREFIX_LEN + 4

# scrypt: ~64 MiB pamäte, použité raz na súbor
SCRYPT_N = 2 ** 16
SCRYPT_R = 8
SCRYPT_P = 1


class DecryptionError(Exception):
    """Zlé heslo, poškodený alebo upravený súbor."""


def passphrase() -> str | None:

    value = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()

    return value or None


def enabled() -> bool:

    return passphrase() is not None


def passphrase_problems(value: str | None) -> list[str]:

    if not value:
        return []

    problems = []

    if len(value) < MIN_PASSPHRASE_LENGTH:
        problems.append(f"BACKUP_ENCRYPTION_KEY je kratší než {MIN_PASSPHRASE_LENGTH} znakov")

    if len(set(value)) < 8:
        problems.append("BACKUP_ENCRYPTION_KEY nie je dosť náhodný")

    return problems


def _derive_key(secret: str, salt: bytes) -> bytes:

    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(secret.encode("utf-8"))


def _aad(header: bytes, counter: int, final: bool) -> bytes:

    return header + struct.pack(">I", counter) + (b"\x01" if final else b"\x00")


def _nonce(prefix: bytes, counter: int) -> bytes:

    return prefix + struct.pack(">I", counter)


def encrypt_file(source: Path, target: Path, secret: str, chunk_size: int = DEFAULT_CHUNK) -> None:

    problems = passphrase_problems(secret)

    if problems or not secret:
        raise ValueError("; ".join(problems) or "chýba heslo na šifrovanie")

    salt = os.urandom(SALT_LEN)
    prefix = os.urandom(NONCE_PREFIX_LEN)
    header = MAGIC + salt + prefix + struct.pack(">I", chunk_size)
    cipher = AESGCM(_derive_key(secret, salt))

    with open(source, "rb") as src, open(target, "wb") as dst:

        dst.write(header)

        counter = 0
        current = src.read(chunk_size)

        while True:

            following = src.read(chunk_size)
            final = not following

            dst.write(cipher.encrypt(_nonce(prefix, counter), current, _aad(header, counter, final)))

            if final:
                break

            current, counter = following, counter + 1


def decrypt_file(source: Path, target: Path, secret: str) -> None:

    with open(source, "rb") as src:

        header = src.read(HEADER_LEN)

        if len(header) != HEADER_LEN or not header.startswith(MAGIC):
            raise DecryptionError("Nie je to zašifrovaná záloha (zlá hlavička).")

        salt = header[len(MAGIC):len(MAGIC) + SALT_LEN]
        prefix = header[len(MAGIC) + SALT_LEN:len(MAGIC) + SALT_LEN + NONCE_PREFIX_LEN]
        chunk_size = struct.unpack(">I", header[-4:])[0]

        if not 1024 <= chunk_size <= 64 * 1024 * 1024:
            raise DecryptionError("Poškodená hlavička zálohy.")

        cipher = AESGCM(_derive_key(secret, salt))
        block = chunk_size + TAG_LEN

        try:

            with open(target, "wb") as dst:

                counter = 0
                current = src.read(block)

                if len(current) < TAG_LEN:
                    raise DecryptionError("Záloha je prázdna alebo odrezaná.")

                while True:

                    following = src.read(block)
                    final = not following

                    dst.write(cipher.decrypt(_nonce(prefix, counter), current, _aad(header, counter, final)))

                    if final:
                        break

                    current, counter = following, counter + 1

        except InvalidTag as exc:

            Path(target).unlink(missing_ok=True)

            raise DecryptionError(
                "Rozšifrovanie zlyhalo: zlé heslo alebo je súbor poškodený či upravený."
            ) from exc


def is_encrypted_backup(data: bytes) -> bool:
    """Zašifrovanú zálohu rozpoznáme podľa hlavičky, nie podľa prípony súboru."""

    return data[:len(MAGIC)] == MAGIC


def decrypt_bytes(data: bytes, secret: str) -> bytes:
    """Rozšifruje zálohu držanú v pamäti (nahraný súbor); chyby ako DecryptionError."""

    import tempfile

    with tempfile.TemporaryDirectory(prefix="dzdec-") as directory:

        source = Path(directory) / "in.enc"
        target = Path(directory) / "out.zip"

        source.write_bytes(data)
        decrypt_file(source, target, secret)

        return target.read_bytes()
