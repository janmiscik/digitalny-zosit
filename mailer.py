"""
Odosielanie e-mailov cez SMTP (len na obnovu hesla). Voliteľné - vypnuté,
kým nie sú nastavené premenné:

    SMTP_HOST       SMTP server (povinné)
    SMTP_PORT       port (predvolené 587; 465 = SSL od začiatku)
    SMTP_USER       prihlasovacie meno (voliteľné)
    SMTP_PASSWORD   heslo (voliteľné)
    SMTP_FROM       adresa odosielateľa (povinné)
    APP_BASE_URL    verejná adresa appky, napr. https://digitalny-zosit-production.up.railway.app
                    (povinné: odkaz na obnovu hesla sa skladá z nej, nie z hlavičky Host,
                    ktorú by mohol podvrhnúť útočník)

Odoslanie nikdy nevyhodí výnimku - vráti False a zapíše chybu do logu.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def enabled() -> bool:

    return all(os.getenv(name, "").strip() for name in ("SMTP_HOST", "SMTP_FROM", "APP_BASE_URL"))


def base_url() -> str:

    return os.getenv("APP_BASE_URL", "").strip().rstrip("/")


def send_mail(to: str, subject: str, body: str) -> bool:

    if not enabled():
        return False

    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"].strip()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = os.environ["SMTP_HOST"].strip()

    try:
        port = int(os.getenv("SMTP_PORT", "587"))

    except ValueError:
        port = 587

    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")

    try:

        context = ssl.create_default_context()

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)

        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls(context=context)

        with server:

            if user:
                server.login(user, password)

            server.send_message(message)

        return True

    except Exception:   # noqa: BLE001 - chyba e-mailu nesmie zhodiť požiadavku

        logger.exception("Odoslanie e-mailu zlyhalo")

        return False
