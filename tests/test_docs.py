"""Dokumentácia neodkazuje na neexistujúce súbory a príkazy z návodov majú existujúce skripty."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "AUDIT.md", "docs/INSTALACIA.md", "docs/KONFIGURACIA.md", "docs/HOSTING.md",
        "docs/OBNOVA.md", "docs/PREVADZKA.md"]
PATH_PATTERN = re.compile(r"`((?:[A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-]+\.(?:py|md|yml|ini|sh|txt|json))`")

# generované alebo ilustračné názvy, ktoré v repozitári byť nemusia
ALLOWED_MISSING = {
    "database.db", "zaloha.zip", "backup-status.json", "restore-journal.json", "accounts.db",
    "server-errors.log", "digitalny-zosit.db", "uploads_utils.py",
}


@pytest.mark.parametrize("doc", DOCS)
def test_documented_files_exist(doc):

    text = (ROOT / doc).read_text(encoding="utf-8")
    missing = []

    for match in PATH_PATTERN.findall(text):

        name = match.split("/")[-1]

        if name in ALLOWED_MISSING or "RRRR" in match or "<" in match:
            continue

        candidates = [ROOT / match, ROOT / "docs" / match, ROOT / "tests" / match]

        if not any(path.exists() for path in candidates):
            missing.append(match)

    assert not missing, f"{doc} odkazuje na neexistujúce súbory: {sorted(set(missing))}"


def test_install_guide_commands_reference_real_scripts():

    text = (ROOT / "docs/INSTALACIA.md").read_text(encoding="utf-8")

    for script in re.findall(r"python (?:\S*/)?([A-Za-z_]+\.py)", text):
        assert (ROOT / script).exists() or (ROOT / "tools" / script).exists(), script


def test_docs_do_not_pin_an_exact_test_count():

    """Presný počet testov by sa v dokumentoch rozchádzal pri každej zmene; píše sa "vyše 1 500"."""

    pattern = re.compile(r"\b\d[\d  ]{2,6}\s*(?:testov|testy|passed|prešlo)\b")
    offenders = []

    for doc in ["README.md", "AUDIT.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))]:

        for number, line in enumerate((ROOT / doc).read_text(encoding="utf-8").splitlines(), start=1):

            match = pattern.search(line)

            if match and not match.group(0).startswith(("1 500", "1500", "723")):  # 723 = historický stav auditu
                offenders.append(f"{doc}:{number}: {match.group(0)}")

    assert offenders == []


def test_license_names_the_rights_holder_without_open_todo():

    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Ján Miščik" in text and "doplň" not in text.lower() and "JanMi" not in text
