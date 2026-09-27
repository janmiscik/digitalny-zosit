"""
Validácia formátu e-mailu a bežných slovenských identifikátorov
(IČO, DIČ, IČ DPH, IBAN).

Zámerne KONZERVATÍVNE - appku môžu používať aj zahraniční zákazníci
alebo firmy s inými formátmi identifikátorov, ktoré nevieme spoľahlivo
overiť. Validácia IČO/DIČ/IČ DPH sa preto aplikuje LEN vtedy, keď
hodnota vyzerá ako typický slovenský formát (napr. presne 8 číslic pre
IČO) - v tom prípade sa navyše overí aj kontrolná číslica, aby appka
odchytila preklepy. Hodnoty, ktoré tomuto vzoru nezodpovedajú
(zahraničné firmy, iné formáty), sa NEODMIETAJÚ - validácia sa cez ne
jednoducho "preskočí" a appka ich necháva prejsť tak, ako sú.

Zdroje/zdôvodnenie kontrolných algoritmov:
- IČO: modulo 11 s váhami 8..2 na prvých 7 číslic - bežne zdokumentovaný
  a používaný algoritmus, zdieľaný ešte z čias Československa (rovnaký
  pre ČR aj SR).
- IBAN: ISO 13616 (medzinárodný štandard, mod-97 kontrolný súčet) -
  funguje pre IBAN akejkoľvek krajiny, nielen slovenský.
- DIČ / IČ DPH: pre slovenské DIČ (10 číslic) nie je verejne
  zdokumentovaný žiadny jednoduchý kontrolný súčet (na rozdiel od
  IČO) - overuje sa preto LEN formát (presný počet číslic), nie
  kontrolná číslica, aby appka omylom neodmietla platné číslo na
  základe neoverenej domnienky o algoritme.
"""

import re


def validate_email_format(value: str) -> str:
    """
    Vráti hodnotu normalizovanú (orezané okrajové medzery, malé
    písmená), ak vyzerá ako platná e-mailová adresa. E-mailové adresy
    sa v praxi porovnávajú a ukladajú bez ohľadu na veľkosť písmen,
    preto appka ukladá jednotne v malých písmenách.

    Zámerne jednoduchý regex, nie plne RFC 5322 kompatibilný - cieľom
    je odchytiť bežné preklepy (chýbajúci zavináč, chýbajúca doména),
    nie odmietať exotické, ale technicky platné tvary e-mailu.
    """

    stripped = value.strip()

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", stripped):

        raise ValueError(
            f"'{value}' nevyzerá ako platná e-mailová adresa."
        )

    return stripped.lower()


def _ico_checksum_is_valid(digits: str) -> bool:

    weights = range(8, 1, -1)  # 8,7,6,5,4,3,2 - pre prvých 7 číslic

    total = sum(
        int(digit) * weight
        for digit, weight in zip(digits[:7], weights)
    )

    remainder = total % 11

    if remainder == 0:
        expected = 1
    elif remainder == 1:
        expected = 0
    else:
        expected = 11 - remainder

    return int(digits[7]) == expected


def validate_ico_format(value: str) -> str:
    """
    Ak hodnota (po odstránení medzier) pozostáva z presne 8 číslic,
    overí sa aj kontrolná číslica (modulo 11). Iné dĺžky/formáty
    (napr. zahraničné registračné čísla) sa neodmietajú.
    """

    stripped = value.strip().replace(" ", "")

    if not stripped.isdigit() or len(stripped) != 8:
        return value

    if not _ico_checksum_is_valid(stripped):

        raise ValueError(
            f"IČO '{value}' nemá platnú kontrolnú číslicu - "
            "skontroluj, či nie je preklep."
        )

    return value


def validate_dic_format(value: str) -> str:
    """
    Ak hodnota vyzerá na slovenské DIČ (samé číslice), musí mať presne
    10 znakov. Iné formáty (napr. zahraničné) sa neodmietajú.
    """

    stripped = value.strip().replace(" ", "")

    if stripped.isdigit() and len(stripped) != 10:

        raise ValueError(
            f"DIČ '{value}' by malo mať 10 číslic (zadaných je "
            f"{len(stripped)})."
        )

    return value


def validate_ic_dph_format(value: str) -> str:
    """
    Slovenské IČ DPH má tvar "SK" + 10 číslic. Iné dvojpísmenové
    predpony (firmy z iných krajín EÚ) sa neodmietajú - overí sa len
    hrubý tvar (kód krajiny + alfanumerický reťazec).
    """

    stripped = value.strip().replace(" ", "").upper()

    if stripped.startswith("SK"):

        digits = stripped[2:]

        if not digits.isdigit() or len(digits) != 10:

            raise ValueError(
                f"IČ DPH '{value}' by pri predpone SK malo mať presne "
                "10 číslic za 'SK' (napr. SK2020123456)."
            )

        return stripped

    if not re.match(r"^[A-Z]{2}[A-Z0-9]{2,13}$", stripped):

        raise ValueError(
            f"'{value}' nevyzerá ako platné IČ DPH (očakáva sa "
            "dvojpísmenový kód krajiny a za ním číslo, "
            "napr. SK2020123456)."
        )

    return stripped


_IBAN_LETTER_VALUES = {
    chr(ord("A") + i): str(10 + i)
    for i in range(26)
}

# Presná dĺžka IBAN podľa krajiny (ISO 13616) - NIE je to úplný zoznam
# všetkých krajín používajúcich IBAN (tých je cez 80), len bežné
# európske krajiny/obchodní partneri. Pre krajinu, ktorá tu nie je
# uvedená, sa použije len všeobecný rozsah dĺžky (15-34 znakov) -
# mod-97 kontrolný súčet nižšie je tak či tak hlavnou obranou proti
# preklepu, toto je len dodatočná (nie vyčerpávajúca) kontrola navyše.
IBAN_LENGTH_BY_COUNTRY = {
    "NO": 15, "BE": 16, "NL": 18, "DK": 18, "FO": 18, "FI": 18,
    "GL": 18, "SD": 18, "MK": 19, "SI": 19, "AT": 20, "BA": 20,
    "EE": 20, "KZ": 20, "XK": 20, "LT": 20, "LU": 20, "HR": 21,
    "LV": 21, "LI": 21, "CH": 21, "BH": 22, "BG": 22, "GE": 22,
    "DE": 22, "IE": 22, "ME": 22, "RS": 22, "GB": 22, "VA": 22,
    "TL": 23, "GI": 23, "IQ": 23, "IL": 23, "AD": 24, "CZ": 24,
    "SK": 24, "ES": 24, "RO": 24, "SE": 24, "PT": 25, "CY": 28,
    "HU": 28, "PL": 28, "AL": 28, "FR": 27, "IT": 27, "MC": 27,
    "SM": 27, "GR": 27, "MT": 31,
}


def validate_iban_format(value: str) -> str:
    """
    Overí formát a kontrolný súčet (mod-97, ISO 13616) IBAN čísla -
    funguje pre IBAN akejkoľvek krajiny. Medzery sa pri kontrole
    ignorujú a do vrátenej hodnoty sa nezahŕňajú (IBAN sa ukladá bez
    medzier, veľkými písmenami).

    Ak je krajina (prvé 2 písmená) v IBAN_LENGTH_BY_COUNTRY, overí sa
    aj presná dĺžka pre danú krajinu - odchytí to preklep v počte
    číslic skôr, než by sa vôbec počítal kontrolný súčet.
    """

    stripped = value.strip().replace(" ", "").upper()

    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$", stripped):

        raise ValueError(
            f"'{value}' nevyzerá ako platné IBAN číslo."
        )

    country = stripped[:2]
    expected_length = IBAN_LENGTH_BY_COUNTRY.get(country)

    if expected_length is not None and len(stripped) != expected_length:

        raise ValueError(
            f"IBAN '{value}' má nesprávnu dĺžku pre krajinu {country} "
            f"(očakáva sa {expected_length} znakov, zadaných je "
            f"{len(stripped)})."
        )

    rearranged = stripped[4:] + stripped[:4]

    numeric = "".join(
        _IBAN_LETTER_VALUES.get(char, char)
        for char in rearranged
    )

    if int(numeric) % 97 != 1:

        raise ValueError(
            f"IBAN '{value}' nemá platný kontrolný súčet - "
            "skontroluj, či nie je preklep."
        )

    return stripped


def normalize_iban(value: str | None) -> str | None:
    """
    "Best-effort" normalizácia IBAN pre VÝSTUPY (napr. Peppol XML) -
    na rozdiel od validate_iban_format NIKDY nevyhodí výnimku, ani keď
    je hodnota technicky neplatná (napr. uložená v appke ešte pred
    zavedením tejto validácie, alebo obnovená zo staršej zálohy).
    Export/PDF sa nesmie zrútiť kvôli historicky uloženým dátam - na
    tvrdú validáciu PRI VSTUPE (formulár) použi validate_iban_format().
    """

    if not value:
        return value

    try:
        return validate_iban_format(value)

    except ValueError:
        return value.strip().replace(" ", "").upper()


def normalize_ic_dph(value: str | None) -> str | None:
    """Rovnaký princíp ako normalize_iban(), pre IČ DPH."""

    if not value:
        return value

    try:
        return validate_ic_dph_format(value)

    except ValueError:
        return value.strip().replace(" ", "").upper()
