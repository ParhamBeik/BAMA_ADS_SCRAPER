"""Canonical car identity: one catalog model per real car.

A real car is Bama's model label plus its powertrain. Bama's labels are clean —
no spelling variants across 842 pairs — but the label alone lumps different
cars: "207" covers TU3, TU5 and TU5P engines with manual and automatic boxes,
"راوفور" a 2.0 and a 2.5 litre. Those differ in price as much as different
models do, so displacement, engine code, turbo, bi-fuel and transmission are part
of the model. Equipment and trim ("پانوراما", "الیت", "تیپ 2") stay the variant.

``taxonomy.csv`` beside this file lists every known ``(brand_fa, label)`` pair
and the label to use; it differs from Bama only where Bama spells one car two
ways ("تی ینا" / "تیانا"). A pair missing from it passes through verbatim and is
marked for review, so a new Bama label surfaces instead of silently becoming a
catalog entry. The brand is always the badge Bama sends.
"""

import csv
import re
from functools import cache
from pathlib import Path

TAXONOMY_CSV = Path(__file__).with_suffix(".csv")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫‌", "0123456789. ")
_DISPLACEMENT = re.compile(r"(?<![\d.])(\d\.\d)(?![\d.])")
_ENGINE_CODE = re.compile(r"\b(TU3|TU5P?|XU7P?|EF7P?|EF4|TUD5|EC5)\b", re.IGNORECASE)
_BIFUEL = re.compile(r"دوگانه.?سوز")


@cache
def _table() -> dict[tuple[str, str], str]:
    with TAXONOMY_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {(row["source_brand"].strip(), row["source_model"].strip()): row["model"].strip()
                for row in csv.DictReader(f)}


def canonical(brand: str | None, label: str | None) -> str | None:
    """The reviewed model label for a known pair, else None."""
    return _table().get(((brand or "").strip(), (label or "").strip()))


def reviewed_brands() -> set[str]:
    return {brand for brand, _ in _table()}


def powertrain(label: str | None, trim: str | None, transmission: str | None) -> str:
    """Engine and gearbox words to append to a model label, e.g. "2.5 لیتر اتوماتیک".

    Read from Bama's free-text trim and its transmission field. A word the label
    already carries ("کوییک، دنده ای S", "دنا، پلاس EF7P") is not repeated.
    """
    label = (label or "").translate(_DIGITS)
    trim = (trim or "").translate(_DIGITS)
    parts: list[str] = []
    if (m := _DISPLACEMENT.search(trim)) and m.group(1) not in label:
        parts.append(f"{m.group(1)} لیتر")
    for code in _ENGINE_CODE.findall(trim):
        code = code.upper()
        if code not in label.upper() and code not in parts:
            parts.append(code)
    if "توربو" in trim and "توربو" not in label:
        parts.append("توربو")
    if _BIFUEL.search(trim) and not _BIFUEL.search(label):
        parts.append("دوگانه سوز")
    gearbox = (transmission or "").translate(_DIGITS).strip()
    stem = {"اتوماتیک": "اتومات", "دنده ای": "دنده"}.get(gearbox)
    if stem and stem not in label:
        parts.append(gearbox)
    return " ".join(parts)


def model_name(brand: str | None, label: str | None, trim: str | None,
               transmission: str | None) -> tuple[str, bool]:
    """``(canonical model name, reviewed)`` for one ad."""
    mapped = canonical(brand, label)
    base = mapped or (label or "").strip()
    return " ".join(p for p in (base, powertrain(base, trim, transmission)) if p), bool(mapped)
