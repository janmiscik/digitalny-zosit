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
