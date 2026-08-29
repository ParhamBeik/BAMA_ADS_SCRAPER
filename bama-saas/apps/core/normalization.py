"""Text normalization and search tokenization for Persian / Arabic inputs.

Handles:
- Persian/Arabic Unicode digits (`۰-۹`, `٠-٩`) <-> ASCII digits (`0-9`)
- Arabic character normalization (`ي` -> `ی`, `ك` -> `ک`, `ة` -> `ه`, `آ/أ/إ` -> `ا`)
- Punctuation stripping (Persian comma `،`, commas, hyphens, ZWNJ `\u200c`)
- Search token extraction for multi-term queries
"""

from __future__ import annotations

import re

_PERSIAN_ARABIC_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ASCII_TO_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_CHAR_MAP = str.maketrans({
    "ي": "ی",
    "ك": "ک",
    "ة": "ه",
    "آ": "ا",
    "أ": "ا",
    "إ": "ا",
    "ؤ": "و",
    "ئ": "ی",
    "\u200c": " ",  # Zero-width non-joiner (نیم‌فاصله) to space
    "،": " ",
    ",": " ",
    "-": " ",
    "_": " ",
    "/": " ",
    "\\": " ",
})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Normalize digits, Arabic letters, punctuation, and whitespace."""
    if not text:
        return ""
    cleaned = text.translate(_PERSIAN_ARABIC_DIGITS).translate(_CHAR_MAP)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def to_persian_digits(text: str) -> str:
    """Translate ASCII digits to Persian digits."""
    if not text:
        return ""
    return text.translate(_ASCII_TO_PERSIAN_DIGITS)


def search_tokens(text: str | None) -> list[str]:
    """Extract distinct search tokens from a raw user query string."""
    norm = normalize_text(text)
    if not norm:
        return []
    return [token for token in norm.split(" ") if token]


# Assembler / Marque aliases in Iran
ASSEMBLER_BRAND_SLUGS: dict[str, list[str]] = {
    "مدیران": ["ام-وی-ام", "فونیکس", "چری", "اکستریم", "لوکانو"],
    "مدیران خودرو": ["ام-وی-ام", "فونیکس", "چری", "اکستریم", "لوکانو"],
    "کرمان": ["کی-ام-سی", "جک", "لیفان"],
    "کرمان موتور": ["کی-ام-سی", "جک", "لیفان"],
    "بهمن": ["بهمن-موتور", "دیگنیتی", "فیدلیتی", "ریسپکت", "اینرودز"],
    "بهمن موتور": ["بهمن-موتور", "دیگنیتی", "فیدلیتی", "ریسپکت", "اینرودز"],
    "آرین": ["لاماری"],
    "آرین موتور": ["لاماری"],
}
