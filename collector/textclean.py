"""Plain text from the HTML fragments Durunubi puts in its text fields."""

from __future__ import annotations

import html
import re
from typing import Any

_BREAK_TAG = re.compile(r"<\s*(?:br|/p|p|/div|div|/li|li)(?:\s[^>]*)?/?\s*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<\s*/?\s*[A-Za-z!][^>]*>")
_SPACES = re.compile(r"[ \t\r\f\v\xa0　]+")


def clean_text(value: Any) -> str:
    """Strip tags, turn `<br>`/`<p>` into line breaks, unescape entities,
    collapse spaces and drop empty lines. None becomes ""."""
    if value is None:
        return ""
    text = _BREAK_TAG.sub("\n", str(value))
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)
