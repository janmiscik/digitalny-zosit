"""Distribučný balík: čistý (bez artefaktov a tajomstiev) a s licenciami komponentov."""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):

    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


dist = load_tool("make_distribution")
notices = load_tool("generate_notices")


def make_tree(root: Path, files: dict):

    for name, content in files.items():

        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())


def names_in(archive_path):

    with zipfile.ZipFile(archive_path) as archive:
        return {name.split("/", 1)[1] for name in archive.namelist()}


def test_package_excludes_artifacts_and_keeps_sources(tmp_path):

    project = tmp_path / "p"
    make_tree(project, {
        "main.py": "print(1)", "README.md": "x", ".env.example": "SECRET_KEY=",
        "static/style.css": "a{}", "docker-entrypoint.sh": "#!/bin/sh",
        # nemá sa dostať dnu:
        ".env": "SECRET_KEY=abc", "data/accounts.db": b"db", "data/accounts/x/database.db": b"db",
        "a.db": b"x", "a.db-wal": b"x", "__pycache__/m.pyc": b"x", "pkg/__pycache__/m.pyc": b"x",
        ".pytest_cache/v": "x", ".pytest_tmp/t": "x", ".venv/lib/x.py": "x", ".git/config": "x",
        "obnova/admin-z.zip": b"x", "server-errors.log": "x", "old.zip": b"x", "htmlcov/index.html": "x",
        "backup.zip.enc": b"x", ".coverage": b"x", ".env.local": "SECRET=1", "dist/old.zip": b"x",
    })

    out = tmp_path / "out" / "balik.zip"

    assert dist.build(out, root=project) == []

    assert names_in(out) == {"main.py", "README.md", ".env.example", "static/style.css", "docker-entrypoint.sh"}
    assert (tmp_path / "out" / "balik.zip.sha256").read_text().split()[0]


@pytest.mark.parametrize("content", [
    "SECRET_KEY=" + "a1b2c3d4" * 6,
    "OFFSITE_BACKUP_SECRET_ACCESS_KEY = '" + "abcdefghijklmnopqrstuvwxyz" + "123456'",
    "aws = AKIA" + "ABCDEFGHIJKLMNOP",
    "-----BEGIN RSA PRIV" + "ATE KEY-----\nabc",
    "token = ghp_" + "A" * 36,
])
def test_package_is_refused_when_a_secret_is_inside(tmp_path, content):

    project = tmp_path / "p"
    make_tree(project, {"main.py": "x", "config.txt": content})

    out = tmp_path / "balik.zip"
    findings = dist.build(out, root=project)

    assert findings and "config.txt" in findings[0]
    assert not out.exists()


def test_placeholders_in_examples_are_not_flagged(tmp_path):

    project = tmp_path / "p"
    make_tree(project, {".env.example": "SECRET_KEY=\n# MONITOR_TOKEN=\nSECRET_KEY=zmen-ma\n"})

    assert dist.build(tmp_path / "b.zip", root=project) == []


def test_real_project_package_is_clean():
    """Skutočný projekt: v balíku nie sú tajomstvá ani zakázané súbory."""

    files = dist.collect_files(ROOT)
    relative = {path.relative_to(ROOT).as_posix() for path in files}

    assert dist.find_secrets(ROOT, files) == []
    assert "LICENSE" in relative and "THIRD_PARTY_NOTICES.md" in relative and "README.md" in relative
    assert not [r for r in relative if r.startswith(("data/", ".git/", ".venv/", "obnova/"))]
    assert not [r for r in relative if r.endswith((".db", ".pyc", ".zip", ".enc")) or r == ".env"]


def test_license_files_exist_and_name_the_rights_holder():

    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Copyright" in text and "Všetky práva vyhradené" in text
    assert (ROOT / "third_party" / "licenses" / "DEJAVU-FONTS-LICENSE.txt").exists()
    assert (ROOT / "third_party" / "licenses" / "OFL-1.1.txt").exists()


def test_notices_list_every_runtime_dependency_with_its_version():

    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    missing = [f"{name}=={version}" for name, version in notices.runtime_requirements()
               if f"| {version} |" not in text or name.lower().replace("_", "-") not in text.lower().replace("_", "-")]

    assert not missing, f"THIRD_PARTY_NOTICES.md je zastaraný, spusti tools/generate_notices.py: {missing}"
    assert "UPOZORNENIE pri generovaní" not in text


def test_no_copyleft_license_among_runtime_dependencies():
    """Silný copyleft (GPL/AGPL...) by sa do predávaného balíka nesmel dostať."""

    import importlib.metadata as md

    offenders = []

    for name, _ in notices.runtime_requirements():

        try:
            meta = md.metadata(name)

        except md.PackageNotFoundError:
            continue

        lic = notices.license_of(name, meta).upper()

        if any(word in lic for word in notices.FORBIDDEN) and "LGPL" not in lic:
            offenders.append((name, lic))

    assert not offenders, offenders


def test_bundled_fonts_are_documented():

    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    for font_file in (ROOT / "static" / "fonts").glob("*.ttf"):
        assert font_file.stem.split("-")[0].replace("DejaVuSans", "DejaVu Sans") in text, font_file.name

    assert "logo-icon.png" in text
