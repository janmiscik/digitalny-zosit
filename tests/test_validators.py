"""
Testy pre validators.py (email, IBAN, IČO, DIČ, IČ DPH).

Kontrolné príklady sú buď matematicky odvodené priamo z algoritmu
(IČO), alebo prevzaté zo štandardných, verejne zdokumentovaných
príkladov (IBAN podľa ISO 13616 - GB82 WEST..., DE89 3704...).
"""

import os
import sys
from pathlib import Path


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


import pytest

from validators import (
    normalize_iban,
    normalize_ic_dph,
    validate_dic_format,
    validate_email_format,
    validate_ic_dph_format,
    validate_ico_format,
    validate_iban_format,
)


# =========================================
# EMAIL
# =========================================

def test_valid_email_passes():
    assert validate_email_format("meno@example.com") == "meno@example.com"


def test_email_is_normalized_to_lowercase_and_trimmed():
    assert validate_email_format("  Test.User@Example.COM  ") == "test.user@example.com"


@pytest.mark.parametrize("bad_email", [
    "nieco-zle",
    "chyba@",
    "@example.com",
    "medzera v@example.com",
    "bez-domeny@",
])
def test_invalid_email_raises(bad_email):

    with pytest.raises(ValueError):
        validate_email_format(bad_email)


# =========================================
# IČO
# =========================================

def test_valid_ico_passes():
    # 10482245 - zdokumentovaný príklad (zvyšok 6, kontrolná číslica 5)
    assert validate_ico_format("10482245") == "10482245"


def test_ico_with_wrong_checksum_raises():

    with pytest.raises(ValueError):
        validate_ico_format("10482244")


def test_ico_with_spaces_is_accepted():
    assert validate_ico_format("10 482 245") == "10 482 245"


@pytest.mark.parametrize("non_slovak_value", [
    "",  # prázdne sa validuje inde (skip pri volaní), tu len formát
    "ABC12345",
    "123456789",  # 9 číslic - nevyzerá na SK IČO, necháva sa prejsť
    "1234567",  # 7 číslic
])
def test_non_8digit_values_are_not_validated(non_slovak_value):
    """Hodnoty, ktoré nevyzerajú na 8-miestne SK IČO (zahraničné
    firmy a pod.), sa musia nechať prejsť bez zásahu."""

    assert validate_ico_format(non_slovak_value) == non_slovak_value


# =========================================
# DIČ
# =========================================

def test_valid_10digit_dic_passes():
    assert validate_dic_format("2020123456") == "2020123456"


def test_dic_with_wrong_digit_count_raises():

    with pytest.raises(ValueError):
        validate_dic_format("202012345")

    with pytest.raises(ValueError):
        validate_dic_format("20201234567")


def test_non_numeric_dic_is_not_validated():
    """Zahraničný formát (nie samé číslice) sa necháva prejsť."""

    assert validate_dic_format("FR1234567890") == "FR1234567890"


# =========================================
# IČ DPH
# =========================================

def test_valid_sk_ic_dph_passes():
    assert validate_ic_dph_format("SK2020123456") == "SK2020123456"


def test_sk_ic_dph_is_normalized_uppercase_no_spaces():
    assert validate_ic_dph_format("sk 2020 123 456") == "SK2020123456"


def test_sk_ic_dph_with_wrong_digit_count_raises():

    with pytest.raises(ValueError):
        validate_ic_dph_format("SK202012345")


def test_foreign_ic_dph_with_plausible_format_passes():
    assert validate_ic_dph_format("DE123456789") == "DE123456789"


def test_ic_dph_without_country_prefix_raises():

    with pytest.raises(ValueError):
        validate_ic_dph_format("2020123456")


# =========================================
# IBAN
# =========================================

def test_valid_sk_iban_passes():
    assert (
        validate_iban_format("SK31 1200 0000 1987 4263 7541")
        == "SK3112000000198742637541"
    )


def test_valid_foreign_ibans_pass():
    # verejne známe, štandardné ISO 13616 dokumentačné príklady
    assert validate_iban_format("GB82 WEST 1234 5698 7654 32") == "GB82WEST12345698765432"
    assert validate_iban_format("DE89 3704 0044 0532 0130 00") == "DE89370400440532013000"


def test_iban_with_wrong_checksum_raises():

    with pytest.raises(ValueError):
        validate_iban_format("SK31 1200 0000 1987 4263 7542")


def test_sk_iban_with_wrong_length_raises():

    with pytest.raises(ValueError):
        # o číslicu menej než platných 24 znakov pre SK
        validate_iban_format("SK3112000000198742637541"[:-1])


def test_iban_completely_malformed_raises():

    with pytest.raises(ValueError):
        validate_iban_format("nieco-uplne-ine")


def test_unknown_country_code_skips_length_check_but_checks_checksum():
    """Pre krajinu mimo IBAN_LENGTH_BY_COUNTRY sa dĺžka nekontroluje,
    ale mod-97 kontrolný súčet stále áno."""

    with pytest.raises(ValueError):
        # XX nie je platný kód krajiny v tabuľke - musí prejsť cez
        # všeobecnú kontrolu formátu/checksumu a zlyhať na checksume
        validate_iban_format("XX0000000000000000")


# =========================================
# normalize_* (best-effort, nikdy nezlyhajú)
# =========================================

def test_normalize_iban_on_valid_value_returns_validated_form():
    assert normalize_iban("sk31 1200 0000 1987 4263 7541") == "SK3112000000198742637541"


def test_normalize_iban_on_invalid_legacy_value_never_raises():
    # zlý checksum - normalize_iban NESMIE zlyhať, len oreže/uppercase-ne
    result = normalize_iban("SK31 1200 0000 1987 4263 7542")
    assert result == "SK3112000000198742637542"


def test_normalize_iban_on_none_returns_none():
    assert normalize_iban(None) is None


def test_normalize_ic_dph_on_invalid_legacy_value_never_raises():
    result = normalize_ic_dph("sk202012345")  # zlý počet číslic
    assert result == "SK202012345"


def test_normalize_ic_dph_on_none_returns_none():
    assert normalize_ic_dph(None) is None
