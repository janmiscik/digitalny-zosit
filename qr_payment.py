"""
Generovanie QR kódu pre platbu podľa slovenského štandardu PAY by square.

Ak sa generovanie z akéhokoľvek dôvodu nepodarí (chýbajúce údaje,
neplatný IBAN a pod.), funkcia vráti None namiesto vyhodenia chyby -
PDF faktúra sa musí vygenerovať aj bez QR kódu.
"""

from decimal import Decimal
from io import BytesIO

import pay_by_square
import qrcode


def generate_payment_qr_image(
    iban: str | None,
    amount: Decimal,
    variable_symbol: str | None,
    beneficiary_name: str | None,
    swift: str | None = None,
    note: str | None = None
) -> BytesIO | None:
    """
    Vygeneruje QR kód pre platbu (PAY by square) ako PNG obrázok v pamäti.

    Vráti None, ak IBAN chýba alebo generovanie zlyhá - volajúci
    (PDF generátor) sa musí vedieť zaobísť aj bez QR kódu.
    """

    if not iban:
        return None

    try:

        payment_string = pay_by_square.generate(
            amount=float(amount),
            iban=iban.replace(" ", ""),
            swift=(swift or "").replace(" ", ""),
            beneficiary_name=beneficiary_name or "",
            variable_symbol=variable_symbol or "",
            note=(note or "")[:140]
        )

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2
        )

        qr.add_data(payment_string)
        qr.make(fit=True)

        img = qr.make_image(
            fill_color="black",
            back_color="white"
        )

        buffer = BytesIO()

        img.save(buffer, format="PNG")

        buffer.seek(0)

        return buffer

    except Exception:

        # QR kód je pekný bonus, nie kritická súčasť faktúry -
        # ak sa nepodarí vygenerovať, PDF sa musí vygenerovať aj bez neho.
        return None
