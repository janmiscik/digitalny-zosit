"""Testy pre uploads_utils.read_upload_with_limit() - priebežné
presadzovanie veľkostného limitu namiesto 'najprv načítaj všetko,
potom over veľkosť'."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

from uploads_utils import read_upload_with_limit


class FakeUpload:
    """
    Simuluje UploadFile: vracia dáta po častiach a počíta, koľkokrát
    bolo .read() zavolané - aby test vedel overiť, že čítanie sa
    SKUTOČNE zastavilo skoro, nie až po prečítaní celého (obrovského)
    súboru.
    """

    def __init__(self, total_size: int, chunk_size: int = 1024):

        self.total_size = total_size
        self.chunk_size = chunk_size
        self.sent = 0
        self.read_calls = 0

    async def read(self, n: int) -> bytes:

        self.read_calls += 1

        remaining = self.total_size - self.sent
        this_chunk = min(n, self.chunk_size, remaining)

        if this_chunk <= 0:
            return b""

        self.sent += this_chunk

        return b"x" * this_chunk


@pytest.mark.anyio
async def test_reads_full_content_within_limit():

    upload = FakeUpload(total_size=5000, chunk_size=1024)

    content = await read_upload_with_limit(
        upload, max_bytes=10_000, too_large_detail="príliš veľké"
    )

    assert len(content) == 5000


@pytest.mark.anyio
async def test_aborts_before_reading_entire_oversized_file():
    """
    Kľúčový test: súbor je oveľa väčší než limit (simuluje napr.
    niekoľko-GB upload pri 200 MB limite). Čítanie sa má zastaviť
    krátko po prekročení limitu, NIE až po prečítaní celého súboru.
    """

    huge_size = 10_000_000_000  # 10 GB
    limit = 1_000_000  # 1 MB

    upload = FakeUpload(total_size=huge_size, chunk_size=1024 * 1024)

    with pytest.raises(HTTPException) as exc_info:
        await read_upload_with_limit(
            upload, max_bytes=limit, too_large_detail="príliš veľké"
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "príliš veľké"

    # Prečítalo sa len o málo viac, než je limit - nie celých 10 GB.
    assert upload.sent < limit * 3
    assert upload.read_calls < 10


@pytest.mark.anyio
async def test_empty_upload_returns_empty_bytes():

    upload = FakeUpload(total_size=0)

    content = await read_upload_with_limit(
        upload, max_bytes=1000, too_large_detail="príliš veľké"
    )

    assert content == b""
