"""Šifrovanie záloh pred nahratím mimo servera (backup_crypto + offsite_backup)."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest

import backup_crypto
import offsite_backup
from backup_crypto import DecryptionError, decrypt_file, encrypt_file
from tests.test_offsite_backup import ENV, FakeS3, isolated, local_file, s3  # noqa: F401

PASS = "toto-je-dlhe-nahodne-heslo-123"
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 2048, 5000, 3 * 1024])
def test_roundtrip_various_sizes(tmp_path, size):

    data = secrets.token_bytes(size)
    (tmp_path / "a").write_bytes(data)

    encrypt_file(tmp_path / "a", tmp_path / "a.enc", PASS, chunk_size=1024)
    decrypt_file(tmp_path / "a.enc", tmp_path / "b", PASS)

    assert (tmp_path / "b").read_bytes() == data


def test_ciphertext_hides_plaintext_and_differs_each_time(tmp_path):

    (tmp_path / "a").write_bytes(b"FAKTURA-ZAKAZNIK-SECRET" * 100)

    encrypt_file(tmp_path / "a", tmp_path / "1", PASS)
    encrypt_file(tmp_path / "a", tmp_path / "2", PASS)

    assert b"ZAKAZNIK" not in (tmp_path / "1").read_bytes()
    assert (tmp_path / "1").read_bytes() != (tmp_path / "2").read_bytes()


def test_wrong_passphrase_fails_and_leaves_no_output(tmp_path):

    (tmp_path / "a").write_bytes(b"data" * 500)
    encrypt_file(tmp_path / "a", tmp_path / "a.enc", PASS)

    with pytest.raises(DecryptionError, match="zlé heslo"):
        decrypt_file(tmp_path / "a.enc", tmp_path / "b", "ine-heslo-ine-heslo-1234")

    assert not (tmp_path / "b").exists()


def _encrypted(tmp_path, size=5000):

    (tmp_path / "a").write_bytes(secrets.token_bytes(size))
    encrypt_file(tmp_path / "a", tmp_path / "a.enc", PASS, chunk_size=1024)

    return (tmp_path / "a.enc").read_bytes()


def test_tampered_byte_is_detected(tmp_path):

    blob = bytearray(_encrypted(tmp_path))
    blob[len(blob) // 2] ^= 1
    (tmp_path / "t").write_bytes(bytes(blob))

    with pytest.raises(DecryptionError):
        decrypt_file(tmp_path / "t", tmp_path / "out", PASS)


def test_truncated_file_is_detected(tmp_path):

    blob = _encrypted(tmp_path)

    # odrezanie posledného bloku a odrezanie na hranici bloku
    for cut in (len(blob) - 10, len(blob) - (5000 % 1024 + 16)):
        (tmp_path / "t").write_bytes(blob[:cut])

        with pytest.raises(DecryptionError):
            decrypt_file(tmp_path / "t", tmp_path / "out", PASS)


def test_reordered_chunks_are_detected(tmp_path):

    blob = _encrypted(tmp_path, size=4096 + 100)
    header, body = blob[:backup_crypto.HEADER_LEN], blob[backup_crypto.HEADER_LEN:]
    block = 1024 + 16
    swapped = body[block:2 * block] + body[:block] + body[2 * block:]
    (tmp_path / "t").write_bytes(header + swapped)

    with pytest.raises(DecryptionError):
        decrypt_file(tmp_path / "t", tmp_path / "out", PASS)


@pytest.mark.parametrize("content", [b"", b"nie som zasifrovane", b"DZENC1"])
def test_garbage_input_is_rejected(tmp_path, content):

    (tmp_path / "t").write_bytes(content)

    with pytest.raises(DecryptionError):
        decrypt_file(tmp_path / "t", tmp_path / "out", PASS)


@pytest.mark.parametrize("value", ["", "krátke", "a" * 25])
def test_weak_passphrase_refused_when_encrypting(tmp_path, value):

    (tmp_path / "a").write_bytes(b"x")

    with pytest.raises(ValueError):
        encrypt_file(tmp_path / "a", tmp_path / "a.enc", value)


# ---------- napojenie na offsite ----------

def test_upload_encrypts_and_roundtrips(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)
    plain = b"citlive faktury a zakaznici" * 50

    result = offsite_backup.upload_backup("firma", local_file(tmp_path, content=plain))

    assert result.ok and result.encrypted and result.verified
    assert result.key.endswith(".zip.enc")

    stored = s3.objects[("moj-bucket", result.key)]["data"]

    assert b"faktury" not in stored and stored != plain

    (tmp_path / "s.enc").write_bytes(stored)
    decrypt_file(tmp_path / "s.enc", tmp_path / "s.zip", PASS)

    assert (tmp_path / "s.zip").read_bytes() == plain


def test_validator_runs_on_decrypted_copy(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)
    seen = []

    def validator(path):
        seen.append(path.read_bytes())
        return []

    offsite_backup.upload_backup("firma", local_file(tmp_path, content=b"obsah"), validator=validator)

    assert seen == [b"obsah"]


def test_failed_restore_test_on_decrypted_copy_fails_upload(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)

    result = offsite_backup.upload_backup(
        "firma", local_file(tmp_path), validator=lambda p: ["poškodená DB"]
    )

    assert result.ok is False and "testom obnovy" in result.error and result.encrypted


def test_bad_passphrase_uploads_nothing_not_even_plaintext(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "krátke")

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok is False and "nič sa nenahralo" in result.error
    assert s3.objects == {}


def test_without_passphrase_stays_plain(s3, tmp_path):

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok and not result.encrypted and result.key.endswith(".zip")


def test_corrupted_encrypted_download_is_detected(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)
    s3.same_size_corruption = True

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok is False


def test_no_temp_files_left_behind(s3, tmp_path, monkeypatch):

    import tempfile

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert list((tmp_path / "tmp").iterdir()) == []


def test_decrypt_cli(tmp_path):

    (tmp_path / "a.zip").write_bytes(b"zaloha" * 300)
    encrypt_file(tmp_path / "a.zip", tmp_path / "a.zip.enc", PASS)

    env = {**os.environ, "BACKUP_ENCRYPTION_KEY": PASS}
    run = lambda *args, **kw: subprocess.run(  # noqa: E731
        [sys.executable, str(ROOT / "decrypt_backup.py"), *args],
        cwd=ROOT, env=kw.get("env", env), capture_output=True, text=True,
    )

    ok = run(str(tmp_path / "a.zip.enc"), str(tmp_path / "out.zip"))
    assert ok.returncode == 0 and (tmp_path / "out.zip").read_bytes() == b"zaloha" * 300

    # existujúci cieľ sa neprepíše
    assert run(str(tmp_path / "a.zip.enc"), str(tmp_path / "out.zip")).returncode == 2

    bad = run(str(tmp_path / "a.zip.enc"), str(tmp_path / "x.zip"),
              env={**os.environ, "BACKUP_ENCRYPTION_KEY": "zle-heslo-zle-heslo-zle-1"})
    assert bad.returncode == 1 and "CHYBA" in bad.stdout
