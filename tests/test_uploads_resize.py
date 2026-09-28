"""Testy pre uploads_utils._resize_and_reencode_photo() - zúžený except."""

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PIL import Image

import uploads_utils
from uploads_utils import _resize_and_reencode_photo


def _png_bytes(size=(50, 50)) -> bytes:

    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")

    return buffer.getvalue()


def test_valid_image_is_reencoded():

    result = _resize_and_reencode_photo(_png_bytes(), "PNG")

    assert result[:8] == b"\x89PNG\r\n\x1a\n"


def test_truncated_image_falls_back_to_original_contents():

    broken = _png_bytes()[:40]

    assert _resize_and_reencode_photo(broken, "PNG") == broken


def test_garbage_falls_back_to_original_contents():

    garbage = b"tot\xc3\xa1lne nie je obrazok"

    assert _resize_and_reencode_photo(garbage, "JPEG") == garbage


def test_unexpected_programming_error_is_not_swallowed(monkeypatch):

    def boom(*args, **kwargs):
        raise RuntimeError("programátorská chyba")

    monkeypatch.setattr(uploads_utils.ImageOps, "exif_transpose", boom)

    with pytest.raises(RuntimeError):
        _resize_and_reencode_photo(_png_bytes(), "PNG")


def test_job_photo_path_rejects_dot_names_and_directories():

    for name in ["..", ".", "...", ".hidden", "../x"]:
        assert uploads_utils.job_photo_path(name) is None, name
