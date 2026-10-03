"""
pytest načíta conftest.py pre daný priečinok skôr než samotné test_*.py
súbory v ňom - vďaka tomu je táto premenná nastavená ešte pred tým, než
ktorýkoľvek test súbor urobí `from main import app` (main.py číta
SESSION_HTTPS_ONLY pri importe, pri nastavovaní SessionMiddleware).

FastAPI TestClient (httpx) posiela requesty cez obyčajné http://, nie
https:// - keby session cookie mala nastavený príznak Secure (čo je
produkčný default https_only=True), httpx by ju pri ďalšom requeste
v rámci toho istého testu vôbec neposlal naspäť a všetky testy, ktoré
sa spoliehajú na to, že zostanú prihlásené cez viac requestov (login
-> ďalšia stránka), by zlyhali stratou session - bez toho, aby to
malo čokoľvek spoločné s tým, čo daný test skutočne overuje. V
produkcii (main.py default) ostáva https_only=True.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import tenancy


@pytest.fixture
def patch_tenant_paths(monkeypatch):
    """
    Factory fixture pre testy, ktoré priamo (mimo HTTP požiadavky cez
    TestClient) volajú funkcie z uploads_utils.py/backup_utils.py -
    tie si teraz cestu k súborom konta zisťujú cez tenancy.py namiesto
    starých natvrdo zapísaných konštánt UPLOADS_DIR/JOB_PHOTOS_DIR/
    BACKUPS_DIR.

    Použitie:

        def test_niečo(patch_tenant_paths, tmp_path):
            patch_tenant_paths(
                db_path=tmp_path / "database.db",
                uploads=tmp_path / "uploads",
                backups=tmp_path / "backups"
            )
            ...

    Testy cez TestClient (skutočná HTTP požiadavka) toto nepotrebujú -
    tenancy.set_current_tenant() sa nastaví automaticky pri prihlásení/
    get_db, presne tak ako v bežiacej appke.
    """

    def _patch(slug="test-tenant", db_path=None, uploads=None, backups=None):

        monkeypatch.setattr(tenancy, "get_current_tenant", lambda: slug)

        if db_path is not None:
            monkeypatch.setattr(
                tenancy, "account_database_path", lambda s: Path(db_path)
            )

        if uploads is not None:
            monkeypatch.setattr(
                tenancy, "account_uploads_dir", lambda s: Path(uploads)
            )

        if backups is not None:
            monkeypatch.setattr(
                tenancy, "account_backups_dir", lambda s: Path(backups)
            )

    return _patch
