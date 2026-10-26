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


def search_document(*parts: str | None) -> str:
    """One normalized document for substring search."""
    return normalize_text(" ".join(part or "" for part in parts))


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


# What buyers type for a car the catalogue spells another way: Latin names, and
# «پرشیا» (the 405 sedan, which Bama files under 405). Each word is searched as
# itself *or* as any of these, so «Corolla» and «پرشیا» find cars. Lower-case keys.
SEARCH_ALIASES: dict[str, list[str]] = {
    "پرشیا": ["405"], "persia": ["405"],
    "corolla": ["کرولا"], "camry": ["کمری"], "prius": ["پریوس"], "yaris": ["یاریس"],
    "pride": ["پراید"], "tiba": ["تیبا"], "saina": ["ساینا"], "quick": ["کوییک", "کوئیک"],
    "dena": ["دنا"], "tara": ["تارا"], "shahin": ["شاهین"], "samand": ["سمند"],
    "tondar": ["تندر"], "peugeot": ["پژو"], "renault": ["رنو"], "toyota": ["تویوتا"],
    "kia": ["کیا"], "hyundai": ["هیوندای"], "mazda": ["مزدا"], "nissan": ["نیسان"],
    "bmw": ["ب ام و"], "mercedes": ["بنز"], "benz": ["بنز"], "audi": ["آئودی", "اودی"],
    "haval": ["هاوال"], "chery": ["چری"], "jac": ["جک"], "lifan": ["لیفان"],
    "geely": ["جیلی"], "changan": ["چانگان"], "byd": ["بی وای دی"], "mvm": ["ام وی ام"],
    "volkswagen": ["فولکس"], "honda": ["هوندا"], "suzuki": ["سوزوکی"],
}


def token_variants(token: str) -> list[str]:
    """The word as typed, plus what the catalogue may call it."""
    return [token, *SEARCH_ALIASES.get(token.lower(), [])]


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


# Who builds a badge, for badges whose maker a buyer searches by. Brands are the
# badge Bama sends (پراید, دنا, فونیکس); this is the secondary attribute that
# lets "سایپا" or "مدیران" still find them. A literal table: a fact about the
# Iranian market, not something to infer.
MANUFACTURER: dict[str, str] = {
    **dict.fromkeys(["سمند", "دنا", "رانا", "تارا", "ری را", "ری‌را", "ریرا", "آریسان",
                     "سورن", "روآ", "پژو", "پیکان"], "ایران خودرو"),
    **dict.fromkeys(["پراید", "تیبا", "کوییک", "ساینا", "شاهین", "آریو", "سهند",
                     "اطلس", "زاگرس"], "سایپا"),
    **dict.fromkeys(["ام وی ام", "فونیکس", "چری", "اکستریم", "لوکانو"], "مدیران خودرو"),
    **dict.fromkeys(["کی ام سی", "جک", "لیفان"], "کرمان موتور"),
    **dict.fromkeys(["دیگنیتی", "فیدلیتی", "ریسپکت", "اینرودز"], "بهمن موتور"),
    "لاماری": "آرین موتور",
}


def ad_search_document(title, model_name, brand_name, description) -> str:
    """The one definition of an ad's ``search_text``, maker included."""
    return search_document(title, model_name, brand_name, MANUFACTURER.get(brand_name or ""),
                           description)
