"""
Kontrola integrity medzi databázou a súbormi na disku (uploads/).

Appka drží referencie na súbory (Company.logo_filename,
Company.signature_filename, JobPhoto.filename) v databáze, ale
samotné súbory ležia mimo nej, na disku v uploads/. Tieto dve strany
sa v princípe môžu rozísť - napr. po ručnom zásahu do priečinka
uploads/, po obnove staršej zálohy bez priložených fotiek, alebo pri
inom probléme (hoci finalize_staged_image a save_job_photo_upload sa
práve tomuto snažia predchádzať - viď ich docstringy v
uploads_utils.py).

check_integrity() vracia nájdené nezrovnalosti v OBOCH smeroch:
- chýbajúce súbory: databáza odkazuje na súbor, ktorý na disku
  neexistuje (referencia "visí do prázdna")
- osirotené súbory: súbor na disku existuje, ale žiadny DB záznam sa
  naň neodkazuje (zbytočne zaberá miesto)

Táto funkcia je čisto READ-ONLY diagnostika - nič sama od seba
neopravuje ani nemaže. Voliteľné vyčistenie osirotených súborov robí
cleanup_orphan_files() nižšie, volané výslovne z /settings/integrity-check
(nikdy automaticky na pozadí).
"""

import os
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import AuditLog, Company, Invoice, JobPhoto, Quote
from uploads_utils import ALLOWED_EXTENSIONS, job_photos_dir, uploads_dir


@dataclass
class MissingFileIssue:
    """DB odkazuje na súbor, ktorý na disku neexistuje."""

    description: str
    filename: str


@dataclass
class OrphanFileIssue:
    """Súbor na disku, na ktorý sa žiadny DB záznam neodkazuje."""

    # cesta relatívna k uploads/ priečinku, napr. "logo.png" alebo
    # "job_photos/job5-abc123.jpg" - používa sa aj ako identifikátor
    # pri mazaní cez cleanup_orphan_files().
    relative_path: str


@dataclass
class NumberGapIssue:
    """V číselnom rade dokladov chýba číslo (zrejme zmazaný návrh)."""

    series: str                 # napr. "Faktúry 2026"
    missing: list[str]          # chýbajúce čísla, napr. ["2026003"]


@dataclass
class AcknowledgedGap:
    """Medzera v číslovaní, ktorú používateľ preveril a zapísal dôvod."""

    number: str
    note: str
    when: str


GAP_ACK_ACTION = "integrity.gap_acknowledged"


@dataclass
class IntegrityReport:

    missing_files: list[MissingFileIssue] = field(default_factory=list)
    orphan_files: list[OrphanFileIssue] = field(default_factory=list)
    # Medzery v číslovaní NIE sú chyba integrity dát (súbory a databáza si
    # zodpovedajú), ale upozornenie na preverenie - samostatne od chýb.
    number_gaps: list[NumberGapIssue] = field(default_factory=list)
    acknowledged_gaps: list[AcknowledgedGap] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Žiadna CHYBA integrity (chýbajúce/osirotené súbory). Upozornenia sa nepočítajú."""

        return not self.missing_files and not self.orphan_files

    @property
    def has_warnings(self) -> bool:
        """Zostali nepreverené medzery v číslovaní."""

        return bool(self.number_gaps)


# Číselné rady dokladov: (regulárny výraz čísla, názov rady, formát čísla)
_SERIES = (
    (re.compile(r"^(\d{4})(\d{3,})$"), "Faktúry", "{year}{seq:03d}"),
    (re.compile(r"^ZF(\d{4})(\d{3,})$"), "Zálohové faktúry", "ZF{year}{seq:03d}"),
    (re.compile(r"^DP(\d{4})(\d{3,})$"), "Dobropisy", "DP{year}{seq:03d}"),
    (re.compile(r"^CP(\d{4})(\d{3,})$"), "Cenové ponuky", "CP{year}{seq:03d}"),
)


def find_number_gaps(numbers: list[str]) -> list[NumberGapIssue]:
    """
    Nájde medzery v súvislých radoch RRRRPPP / ZFRRRRPPP / DPRRRRPPP /
    CPRRRRPPP (každý rok a typ má vlastný rad od 001). Čísla, ktoré nemajú
    tento tvar (ručne zadané), sa ignorujú.
    """

    found: dict[tuple[str, str, str], set[int]] = {}

    for number in numbers:

        for pattern, label, fmt in _SERIES:

            match = pattern.match(number or "")

            if match:

                found.setdefault((label, match.group(1), fmt), set()).add(int(match.group(2)))

                break

    issues = []

    for (label, year, fmt), sequences in sorted(found.items(), key=lambda item: (item[0][1], item[0][0])):

        missing = [
            fmt.format(year=year, seq=seq)
            for seq in range(1, max(sequences) + 1)
            if seq not in sequences
        ]

        if missing:
            issues.append(NumberGapIssue(series=f"{label} {year}", missing=missing))

    return issues


def check_integrity(db: Session) -> IntegrityReport:

    report = IntegrityReport()

    referenced_top_level: set[str] = set()
    referenced_job_photos: set[str] = set()

    # --- Company: logo, podpis ---
    company = db.query(Company).first()

    if company is not None:

        for label, filename in (
            ("Logo firmy", company.logo_filename),
            ("Podpis", company.signature_filename),
        ):

            if not filename:
                continue

            referenced_top_level.add(filename)

            if not (uploads_dir() / filename).exists():

                report.missing_files.append(
                    MissingFileIssue(
                        description=label,
                        filename=filename
                    )
                )

    # --- Fotky zákaziek ---
    for photo in db.query(JobPhoto).all():

        referenced_job_photos.add(photo.filename)

        if not (job_photos_dir() / photo.filename).exists():

            report.missing_files.append(
                MissingFileIssue(
                    description=f"Fotka zákazky #{photo.job_id}",
                    filename=photo.filename
                )
            )

    # --- Osirotené súbory: uploads/ (logo/podpis), mimo job_photos/ ---
    if uploads_dir().exists():

        for path in uploads_dir().iterdir():

            if path.is_dir():
                continue

            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            if path.name not in referenced_top_level:

                report.orphan_files.append(
                    OrphanFileIssue(relative_path=path.name)
                )

    # --- Osirotené fotky zákaziek ---
    if job_photos_dir().exists():

        for path in job_photos_dir().iterdir():

            if path.is_dir():
                continue

            if path.name not in referenced_job_photos:

                report.orphan_files.append(
                    OrphanFileIssue(
                        relative_path=f"job_photos/{path.name}"
                    )
                )

    # --- Medzery v číslovaní dokladov ---
    all_gaps = find_number_gaps(
        [row[0] for row in db.query(Invoice.invoice_number).all()]
        + [row[0] for row in db.query(Quote.quote_number).all()]
    )

    acknowledged = acknowledged_gap_notes(db)

    for gap in all_gaps:

        still_open = [number for number in gap.missing if number not in acknowledged]

        if still_open:
            report.number_gaps.append(NumberGapIssue(series=gap.series, missing=still_open))

        for number in gap.missing:

            if number in acknowledged:
                note, when = acknowledged[number]
                report.acknowledged_gaps.append(AcknowledgedGap(number=number, note=note, when=when))

    return report


def acknowledged_gap_notes(db: Session) -> dict[str, tuple[str, str]]:
    """Preverené medzery z audit_log: {číslo: (dôvod, dátum)}; novší záznam prepíše starší."""

    notes: dict[str, tuple[str, str]] = {}

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action == GAP_ACK_ACTION)
        .order_by(AuditLog.id)
        .all()
    )

    for row in rows:

        number, _, note = (row.detail or "").partition("|")

        if number:
            created = getattr(row, "created_at", None)
            notes[number] = (note, created.strftime("%d.%m.%Y") if created else "")

    return notes


def cleanup_orphan_files(db: Session) -> list[str]:
    """
    Zmaže súbory, ktoré check_integrity() PRÁVE TERAZ (nie podľa
    staršieho reportu z obrazovky) vyhodnotí ako osirotené, a vráti
    zoznam zmazaných relatívnych ciest.

    Kontrola sa vždy spúšťa nanovo priamo tu (nie podľa reportu, ktorý
    si používateľ prezerá v prehliadači) - medzi zobrazením reportu a
    kliknutím na "Vyčistiť" mohol pribudnúť nový, legitímny súbor
    (napr. niekto medzitým nahral fotku), ktorý by inak bol mylne
    zmazaný ako "osirotený".
    """

    report = check_integrity(db)

    deleted: list[str] = []

    for issue in report.orphan_files:

        path = uploads_dir() / issue.relative_path

        try:
            os.remove(path)

        except OSError:
            continue

        deleted.append(issue.relative_path)

    return deleted
