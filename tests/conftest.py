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


os.environ.setdefault("SESSION_HTTPS_ONLY", "false")
