"""
Vyhľadávanie údajov o firme/živnostníkovi podľa IČO.

Používa ORSF (Open Register of Slovak Companies) - nezávislý agregátor
slovenských registrov (RPO, ORSR, RÚZ, Finančná správa), ktorý v jednom
volaní vráti meno, adresu, DIČ aj IČ DPH. Nie je to oficiálny štátny
register, len projekt tretej strany v beta verzii bez SLA.

Preto je to v appke navrhnuté VÝSLOVNE ako pohodlie navyše, nikdy nie
ako kritická závislosť: každá chyba (výpadok, timeout, neplatné IČO,
firma sa nenašla, zmena formátu odpovede...) sa potichu premení na
None a používateľ jednoducho vyplní údaje ručne, presne ako predtým.
Appka sa kvôli tomuto vyhľadávaniu nikdy nezastaví ani nespadne.
"""

import httpx


ORSF_API_BASE = "https://api.orsf.sk/v1"

LOOKUP_TIMEOUT_SECONDS = 5.0


def _is_plausible_ico(ico: str) -> bool:

    return ico.isdigit() and len(ico) in (6, 7, 8)


def lookup_company_by_ico(ico: str) -> dict | None:
    """
    Vráti dict s kľúčmi name, address, ico, dic, ic_dph, alebo None,
    ak sa firma nenašla alebo vyhľadávanie z akéhokoľvek dôvodu zlyhalo.

    `ic_dph` je None, ak subjekt nie je (aktuálne) platiteľom DPH.
    `address` je zložená z ulice, mesta a PSČ do jedného textového
    poľa (tak, ako to appka pre zákazníkov ukladá).
    """

    ico = (ico or "").strip()

    if not _is_plausible_ico(ico):
        return None


    try:

        response = httpx.get(
            f"{ORSF_API_BASE}/companies/{ico}",
            timeout=LOOKUP_TIMEOUT_SECONDS,
            headers={"Accept": "application/json"}
        )

    except httpx.HTTPError:

        return None


    if response.status_code != 200:

        return None


    try:

        data = response.json()

    except ValueError:

        return None


    if not isinstance(data, dict):

        return None


    name = data.get("name")

    if not name:

        return None


    address_parts = [
        part.strip()
        for part in (
            data.get("street"),
            data.get("city"),
        )
        if part and part.strip()
    ]

    zip_code = data.get("psc") or data.get("postalCode")

    if zip_code:

        address_parts.append(str(zip_code).replace(" ", ""))

    address = ", ".join(address_parts) if address_parts else None


    vat_registration = data.get("vatRegistration")

    ic_dph = None

    if isinstance(vat_registration, dict):

        ic_dph = (
            vat_registration.get("icDph")
            or vat_registration.get("vatId")
        )


    return {
        "name": name,
        "address": address,
        "ico": data.get("ico") or data.get("nationalId") or ico,
        "dic": data.get("dic") or data.get("taxId"),
        "ic_dph": ic_dph,
    }
