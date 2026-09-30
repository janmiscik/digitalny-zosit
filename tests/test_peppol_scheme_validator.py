"""Testy pre validators.validate_peppol_scheme_id()."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validators import validate_peppol_scheme_id


def test_slovak_scheme_accepted_for_sk():
    assert validate_peppol_scheme_id("9950", expected_country_code="SK") == "9950"


def test_portugal_scheme_rejected_for_sk():
    """Pôvodná chyba: 9946 je PT:VAT, nie slovenská schéma."""

    with pytest.raises(ValueError) as exc:
        validate_peppol_scheme_id("9946", expected_country_code="SK")

    assert "9950" in str(exc.value)


def test_czech_scheme_accepted_for_cz():
    assert validate_peppol_scheme_id("9929", expected_country_code="CZ") == "9929"


def test_bad_format_rejected():
    for bad in ["995", "99500", "99-0", "", "  "]:
        with pytest.raises(ValueError):
            validate_peppol_scheme_id(bad)


def test_unknown_scheme_not_rejected_without_country_conflict():
    assert validate_peppol_scheme_id("0088") == "0088"


def test_no_scheme_is_listed_as_both_valid_and_removed():
    """
    Regresný test na konkrétny nájdený nesúlad: "9901" bola vo
    PEPPOL_EAS_SCHEMES uvedená ako platná (DK:CPR), zatiaľ čo mala byť
    v REMOVED_EAS_SCHEMES (OpenPeppol ju odstránil 30.11.2023) - appka
    teda dovolila zadať hodnotu, ktorú by vzápätí sama nahlásila ako
    chybu. Oba zoznamy teraz žijú v jednom module (validators.py), ale
    tento test by odhalil, keby sa niekedy v budúcnosti opäť rozišli.
    """

    from validators import PEPPOL_EAS_SCHEMES, REMOVED_EAS_SCHEMES

    overlap = set(PEPPOL_EAS_SCHEMES) & REMOVED_EAS_SCHEMES

    assert overlap == set(), (
        f"Tieto schémy sú súčasne 'platné' aj 'odstránené': {overlap}"
    )


def test_9901_is_rejected_at_input_time_not_only_at_xml_export():
    """
    DK:CPR (9901) bola z EAS číselníka odstránená 30.11.2023 - appka ju
    má odmietnuť hneď pri ukladaní formulára (validate_peppol_scheme_id),
    nie až peppol_validation.py po vygenerovaní XML.
    """

    with pytest.raises(ValueError, match="odstránená"):
        validate_peppol_scheme_id("9901", expected_country_code="DK")


def test_removed_scheme_rejected_even_without_expected_country():

    with pytest.raises(ValueError, match="odstránená"):
        validate_peppol_scheme_id("0037")
