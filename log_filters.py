"""
Filtre logov: tajné časti URL sa nesmú dostať do prístupových logov.

Odkaz na obnovu hesla (`/reset-password/<token>`) nesie token priamo v ceste.
Uvicorn každú požiadavku zapíše do `uvicorn.access`, takže by token zostal v
logoch (Railway ich uchováva a vidia ich všetci s prístupom k projektu). Filter
token v zázname nahradí, ešte pred zápisom.

Poznámka: prístupové logy samotnej platformy (reverse proxy) appka ovplyvniť
nevie. Token je preto jednorazový a platí 1 hodinu.
"""

import logging
import re

REDACTED = "[skryte]"

_PATTERN = re.compile(r"(/reset-password/)[^\s/?#\"']+")


def redact(text: str) -> str:

    return _PATTERN.sub(rf"\1{REDACTED}", text)


class RedactResetTokens(logging.Filter):
    """Nahradí token v URL (aj v argumentoch záznamu aj v samotnej správe)."""

    def filter(self, record: logging.LogRecord) -> bool:

        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)

        elif isinstance(record.args, dict):
            record.args = {
                key: redact(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }

        return True


def install() -> None:
    """Pripojí filter k logom, v ktorých sa objavujú URL požiadaviek."""

    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):

        logger = logging.getLogger(name)

        if not any(isinstance(item, RedactResetTokens) for item in logger.filters):
            logger.addFilter(RedactResetTokens())
