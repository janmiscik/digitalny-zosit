"""
CSP etapa 3: šablóny nesmú obsahovať inline skripty ani inline handlery (onclick...).
Všetka logika je v static/js/ a obsluhuje sa delegovane cez data-action atribúty.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
JS = ROOT / "static" / "js"


def read_all():

    return {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html")}


def test_no_inline_script_blocks():

    bad = {n: len(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", t, flags=re.I)) for n, t in read_all().items()}

    assert not {n: c for n, c in bad.items() if c}


def test_no_inline_handlers():

    bad = {n: len(re.findall(r"\son[a-z]+\s*=", t, flags=re.I)) for n, t in read_all().items()}

    assert not {n: c for n, c in bad.items() if c}


def test_every_template_loads_app_js():

    missing = [n for n, t in read_all().items() if "js/app.js" not in t and "{% extends" not in t and "{% include" not in t]

    assert not missing, missing


def test_script_files_exist():

    for name, text in read_all().items():
        for match in re.findall(r"url_for\('static',\s*path='([^']+\.js)'\)", text):
            assert (ROOT / "static" / match).exists(), f"{name}: chýba {match}"


def test_every_data_action_is_registered():

    registered = set()
    for path in JS.rglob("*.js"):
        registered |= set(re.findall(r'["\']?([a-z][a-z0-9-]+)["\']?\s*:\s*(?:\(|function|async|\w+)', path.read_text(encoding="utf-8")))

    used = set()
    for text in read_all().values():
        used |= set(re.findall(r'data-(?:change-)?action="([a-z0-9-]+)"', text))

    assert not (used - registered), sorted(used - registered)
