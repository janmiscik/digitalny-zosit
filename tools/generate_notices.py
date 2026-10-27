"""
Vygeneruje THIRD_PARTY_NOTICES.md: zoznam komponentov tretích strán (Python balíky z
requirements.txt, fonty, logo) s licenciami a plnými textami licencií.

    python tools/generate_notices.py            zapíše THIRD_PARTY_NOTICES.md
    python tools/generate_notices.py --check    overí, že súbor obsahuje všetky balíky (pre CI)

Spúšťa sa v prostredí, kde sú nainštalované balíky z requirements.txt (texty licencií
sa čítajú z ich metadát). Po zmene requirements.txt spusti znova.
"""

import importlib.metadata as md
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.md"
LICENSE_DIR = ROOT / "third_party" / "licenses"
FORBIDDEN = ("GPL", "AGPL", "SSPL", "BUSL", "COMMONS CLAUSE")    # silný copyleft sa nesmie dostať do balíka

# licencie, ktorých metadáta sú nejednotné - doplnené podľa projektov
OVERRIDES = {
    "qrcode": "BSD-3-Clause",
    "colorama": "BSD-3-Clause",
    "itsdangerous": "BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
    "python-dateutil": "BSD-3-Clause OR Apache-2.0",
    "reportlab": "BSD-3-Clause",
    "pillow": "MIT-CMU (HPND)",
}


def runtime_requirements() -> list[tuple[str, str]]:

    rows = []

    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():

        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)", line.strip())

        if match:
            rows.append((match.group(1), match.group(2)))

    return rows


def license_of(name: str, meta) -> str:

    if name.lower() in OVERRIDES:
        return OVERRIDES[name.lower()]

    value = meta.get("License-Expression") or ""

    if not value:

        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License")]
        value = ", ".join(classifiers) or (meta.get("License") or "").splitlines()[0][:80]

    return value or "NEUVEDENÁ"


def license_files(name: str) -> list[tuple[str, str]]:

    files = []

    try:
        dist = md.distribution(name)

    except md.PackageNotFoundError:
        return files

    for path in dist.files or []:

        base = path.name.upper()

        if base.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")) and "test" not in str(path).lower():

            try:
                files.append((path.name, dist.locate_file(path).read_text(encoding="utf-8", errors="replace").strip()))

            except OSError:
                continue

    return files


def build() -> str:

    out = [
        "# Komponenty tretích strán a pôvod materiálov\n",
        "Súbor je generovaný príkazom `python tools/generate_notices.py` (nepíš doň ručne, zmeny rob v skripte). "
        "Aplikácia samotná je pod licenciou v súbore `LICENSE`; komponenty nižšie majú vlastné licencie.\n",
        "## Python balíky (runtime, requirements.txt)\n",
        "| Balík | Verzia | Licencia |", "|---|---|---|",
    ]

    problems = []
    details = []

    for name, version in runtime_requirements():

        try:
            meta = md.metadata(name)

        except md.PackageNotFoundError:

            problems.append(f"{name}: nie je nainštalovaný, nedá sa zistiť licencia")
            out.append(f"| {name} | {version} | NEZISTENÁ |")
            continue

        lic = license_of(name, meta)

        if any(word in lic.upper() for word in FORBIDDEN):
            problems.append(f"{name}: nepovolená licencia {lic}")

        out.append(f"| {meta['Name']} | {version} | {lic} |")

        texts = license_files(name)
        block = [f"### {meta['Name']} {version} - {lic}\n"]
        block += [f"**{file_name}**\n\n```\n{text}\n```\n" for file_name, text in texts] or ["(súbor s licenciou nie je v balíku; pozri projekt balíka)\n"]
        details.append("\n".join(block))

    out += [
        "",
        "Poznámky: `certifi` je pod MPL-2.0 (súborový copyleft: úpravy súborov certifi by sa museli zverejniť; "
        "nepoužívame ich upravené). `qrcode` má v metadátach nejednotné označenie, projekt je pod BSD licenciou.\n",
        "## Fonty\n",
        "| Font | Použitie | Licencia | Spôsob dodania |", "|---|---|---|---|",
        "| DejaVu Sans, DejaVu Sans Bold | PDF dokumenty (`static/fonts/`) | Bitstream Vera / DejaVu (voľná, povoľuje predaj ako súčasť balíka) | priložené v balíku, text: `third_party/licenses/DEJAVU-FONTS-LICENSE.txt` |",
        "| Space Grotesk | webové rozhranie | SIL OFL 1.1 | NIE JE v balíku, načíta sa zo služby Google Fonts (`static/style.css`) |",
        "| Inter | webové rozhranie | SIL OFL 1.1 | NIE JE v balíku, načíta sa zo služby Google Fonts |",
        "| IBM Plex Mono | webové rozhranie | SIL OFL 1.1 | NIE JE v balíku, načíta sa zo služby Google Fonts |",
        "",
        "Poznámky k fontom z Google Fonts: pri každom načítaní stránky sa prehliadač pripája na servery Google "
        "(prenáša sa IP adresa návštevníka), čo môže byť problém z hľadiska ochrany osobných údajov (GDPR). "
        "Odporúčanie pre produkčné nasadenie: fonty stiahnuť a servírovať z `static/` (a zároveň upraviť CSP, "
        "z ktorej potom odpadne výnimka pre `fonts.googleapis.com` a `fonts.gstatic.com`). Text OFL 1.1: "
        "`third_party/licenses/OFL-1.1.txt`.\n",
        "## Logo a grafika\n",
        "| Súbor | Pôvod | Stav |", "|---|---|---|",
        "| `static/logo-icon.png` | vygenerované nástrojom (názov nástroja a jeho podmienky použitia treba doplniť) | **DOPLNIŤ PRED ODOVZDANÍM:** názov nástroja, dátum vzniku a overenie, že jeho podmienky povoľujú komerčné použitie a prevod |",
        "",
        "Iné obrázky, ikony ani skripty tretích strán (jQuery, CDN a pod.) aplikácia neobsahuje; všetok JavaScript je vlastný (`static/js/`).\n",
        "## Nástroje na vývoj\n",
        "Balíky z `requirements-dev.txt` (pytest, pypdf, pygments a pod.) sa neinštalujú do produkčného obrazu a nie sú súčasťou nasadenej aplikácie.\n",
        "## Texty licencií balíkov\n",
    ]

    out += details

    if problems:
        out.insert(2, "> UPOZORNENIE pri generovaní: " + "; ".join(problems) + "\n")

    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:

    text = build()

    if "--check" in argv:

        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        missing = [f"{n}=={v}" for n, v in runtime_requirements() if f"| {v} |" not in current or n.lower() not in current.lower()]

        if missing or "UPOZORNENIE pri generovaní" in text:
            print("THIRD_PARTY_NOTICES.md je neúplný alebo zastaraný:", missing or "problémy s licenciami")
            return 1

        return 0

    OUTPUT.write_text(text, encoding="utf-8")
    print(f"Zapísané: {OUTPUT} ({len(text) // 1024} kB)")

    return 1 if "UPOZORNENIE pri generovaní" in text else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
