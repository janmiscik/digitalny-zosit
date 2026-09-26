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
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import Company, JobPhoto
from uploads_utils import ALLOWED_EXTENSIONS, JOB_PHOTOS_DIR, UPLOADS_DIR


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
class IntegrityReport:

    missing_files: list[MissingFileIssue] = field(default_factory=list)
    orphan_files: list[OrphanFileIssue] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.missing_files and not self.orphan_files


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

            if not (UPLOADS_DIR / filename).exists():

                report.missing_files.append(
                    MissingFileIssue(
                        description=label,
                        filename=filename
                    )
                )

    # --- Fotky zákaziek ---
    for photo in db.query(JobPhoto).all():

        referenced_job_photos.add(photo.filename)

        if not (JOB_PHOTOS_DIR / photo.filename).exists():

            report.missing_files.append(
                MissingFileIssue(
                    description=f"Fotka zákazky #{photo.job_id}",
                    filename=photo.filename
                )
            )

    # --- Osirotené súbory: uploads/ (logo/podpis), mimo job_photos/ ---
    if UPLOADS_DIR.exists():

        for path in UPLOADS_DIR.iterdir():

            if path.is_dir():
                continue

            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            if path.name not in referenced_top_level:

                report.orphan_files.append(
                    OrphanFileIssue(relative_path=path.name)
                )

    # --- Osirotené fotky zákaziek ---
    if JOB_PHOTOS_DIR.exists():

        for path in JOB_PHOTOS_DIR.iterdir():

            if path.is_dir():
                continue

            if path.name not in referenced_job_photos:

                report.orphan_files.append(
                    OrphanFileIssue(
                        relative_path=f"job_photos/{path.name}"
                    )
                )

    return report


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

        path = UPLOADS_DIR / issue.relative_path

        try:
            os.remove(path)

        except OSError:
            continue

        deleted.append(issue.relative_path)

    return deleted
