"""
Čistý distribučný balík projektu (ZIP) bez lokálnych artefaktov, testovacích výstupov, dát a tajomstiev.

    python tools/make_distribution.py                 -> dist/digitalny-zosit-<dátum>.zip + .sha256
    python tools/make_distribution.py --out iny.zip

Balík neobsahuje: databázy a dáta kont (data/), .env, virtuálne prostredie, cache (__pycache__,
.pytest_cache, .pytest_tmp), pokryté výstupy testov, obnovené zálohy (obnova/), logy, .git a už
vytvorené balíky. Pred zápisom prehľadá obsah, či v ňom nie sú zjavné tajomstvá (kľúče, tokeny);
ak áno, balík nevznikne.
"""

import hashlib
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".pytest_tmp", ".mypy_cache",
    ".ruff_cache", "htmlcov", "node_modules", "data", "obnova", "dist", "build", ".idea", ".vscode",
    ".claude", ".tox",
}
EXCLUDED_NAMES = {".env", ".coverage", "server-errors.log", "Thumbs.db", ".DS_Store", "coverage.xml"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".zip", ".enc", ".tmp", ".bak")
EXCLUDED_PREFIXES = (".env.",)
ALLOWED_EXCEPTIONS = {".env.example"}

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "súkromný kľúč"),
    (re.compile(r"(?im)^\s*(?:export\s+)?(?:SECRET_KEY|MONITOR_TOKEN|BACKUP_ENCRYPTION_KEY|"
                r"OFFSITE_BACKUP_SECRET_ACCESS_KEY|SMTP_PASSWORD)\s*=\s*['\"]?[A-Za-z0-9_\-/+=]{24,}"),
     "tajná hodnota priradená v súbore"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "GitHub token"),
]


def is_excluded(relative: Path) -> bool:

    parts = relative.parts

    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True

    name = parts[-1]

    if name in ALLOWED_EXCEPTIONS:
        return False

    return (
        name in EXCLUDED_NAMES
        or name.endswith(EXCLUDED_SUFFIXES)
        or name.startswith(EXCLUDED_PREFIXES)
    )


def collect_files(root: Path = ROOT) -> list[Path]:

    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not is_excluded(path.relative_to(root))
    )


def find_secrets(root: Path, files: list[Path]) -> list[str]:

    findings = []

    for path in files:

        if path.stat().st_size > 2_000_000:
            continue

        try:
            text = path.read_text(encoding="utf-8")

        except (UnicodeDecodeError, OSError):
            continue

        for pattern, label in SECRET_PATTERNS:

            if pattern.search(text):
                findings.append(f"{path.relative_to(root)}: {label}")

    return findings


def build(output: Path, root: Path = ROOT) -> list[str]:
    """Vytvorí balík; vráti zoznam nálezov (ak nie je prázdny, balík sa nezapíše)."""

    files = collect_files(root)
    findings = find_secrets(root, files)

    if findings:
        return findings

    output.parent.mkdir(parents=True, exist_ok=True)
    top = "digitalny-zosit"

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:

        for path in files:

            info = zipfile.ZipInfo(f"{top}/{path.relative_to(root).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == ".sh" else 0o644) << 16

            archive.writestr(info, path.read_bytes())

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    Path(str(output) + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")

    return []


def main(argv: list[str]) -> int:

    output = ROOT / "dist" / f"digitalny-zosit-{date.today().isoformat()}.zip"

    if "--out" in argv:
        output = Path(argv[argv.index("--out") + 1]).resolve()

    findings = build(output)

    if findings:

        print("Balík NEVZNIKOL, v súboroch sú možné tajomstvá:")

        for finding in findings:
            print("  -", finding)

        return 1

    print(f"Hotovo: {output} ({output.stat().st_size // 1024} kB), kontrolný súčet v {output.name}.sha256")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
