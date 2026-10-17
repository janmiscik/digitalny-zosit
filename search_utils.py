"""
Vyhľadávanie bez ohľadu na diakritiku a veľkosť písmen.

SQLite `LIKE`/`ilike` porovnáva bez ohľadu na veľkosť písmen len pre
ASCII - "šimon" by nenašlo "Šimon" a "simon" už vôbec nie. Pre slovenské
mená a názvy je to nepoužiteľné (najmä pri písaní na mobile bez
diakritiky), preto sa hľadanie robí v Pythone nad už vyfiltrovanými
záznamami (stav, obdobie...). Pre objem dát jedného remeselníka je to
bohato dosť rýchle.
"""

import unicodedata


def fold(text) -> str:
    """Malé písmená bez diakritiky: 'Šimon Ďurčo' -> 'simon durco'."""

    if text is None:
        return ""

    decomposed = unicodedata.normalize("NFKD", str(text))

    without_marks = "".join(
        char for char in decomposed
        if not unicodedata.combining(char)
    )

    return without_marks.casefold()


def matches_search(term: str | None, *fields) -> bool:
    """
    True, ak sa KAŽDÉ slovo hľadaného výrazu nachádza v niektorom z
    polí (poradie slov nezáleží): 'novak kotol' nájde zákazníka Novák
    s položkou 'Oprava kotla'... resp. akúkoľvek kombináciu polí.
    Prázdny výraz vyhovuje všetkému.
    """

    words = fold(term).split()

    if not words:
        return True

    haystack = " ".join(fold(field) for field in fields if field)

    return all(word in haystack for word in words)
