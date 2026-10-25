"""
Sprísnenie CSP po etapách: počet inline skriptov a handlerov v šablónach sa nesmie ZVYŠOVAŤ.
Pri presune do static/ sa limity znižujú; cieľ je 0 (potom sa môže zrušiť 'unsafe-inline' v script-src).
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

# aktuálny stav (etapa 1) - pri každom kroku presunu hodnoty zníž
MAX_INLINE_SCRIPT_BLOCKS = 28
MAX_INLINE_HANDLERS = 59


def read_all():

    return {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html")}


def inline_script_blocks(text: str) -> int:

    return len(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", text, flags=re.IGNORECASE))


def inline_handlers(text: str) -> int:

    return len(re.findall(r"\son[a-z]+\s*=", text, flags=re.IGNORECASE))


def test_inline_scripts_do_not_grow():

    total = sum(inline_script_blocks(text) for text in read_all().values())

    assert total <= MAX_INLINE_SCRIPT_BLOCKS, f"inline <script> blokov: {total} (limit {MAX_INLINE_SCRIPT_BLOCKS})"


def test_inline_handlers_do_not_grow():

    total = sum(inline_handlers(text) for text in read_all().values())

    assert total <= MAX_INLINE_HANDLERS, f"inline handlerov (onclick...): {total} (limit {MAX_INLINE_HANDLERS})"
