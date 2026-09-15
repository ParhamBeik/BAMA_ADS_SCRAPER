"""Pure-Python parsing of a raw bama.ir ad payload. No Django, no ORM.

Everything here turns one JSON blob from the feed into the values ingestion
stores: flat columns (`extract_ad`), a calendar-normalised model year, a
mileage that keeps a real zero, hashes for change detection, and the publish
time behind phrases like "۲ ساعت پیش".
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import jdatetime

# ---------------------------------------------------------------------------
# Where an ad lives on the source site
# ---------------------------------------------------------------------------

SITE_ROOT = "https://bama.ir"

# Hosts a listing photo is allowed to come from. The real CDN is a numbered
# subdomain (`cdn-sth1.bama.ir`), which the suffix match covers.
#
# Lives here, with the other facts about the source site, because it has two
# consumers that must agree: ingest, which decides what to store, and the image
# proxy, which re-checks before turning a stored value into an outbound request.
# It used to be private to ingest and imported through the underscore, which
# both broke encapsulation and coupled the read path to the writer.
CDN_HOSTS = ("cdn.bama.ir", "bama.ir", "media.bama.ir")


def is_cdn_url(url: str) -> bool:
    """True for an HTTPS URL served from Bama's own image CDN.

    The guard on the one path that turns stored data into an outbound HTTP
    request, so it is deliberately strict: HTTPS only, and the host must be the
    apex or a subdomain of an allowlisted domain — never a substring match,
    which `bama.ir.evil.com` would pass.
    """
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    try:
        host = url.split("/")[2].lower()
    except IndexError:
        return False
    return any(host == h or host.endswith("." + h) for h in CDN_HOSTS)


def absolute_ad_url(path: str | None) -> str:
    """The ad's real address on bama.ir.

    Bama sends ``detail.url`` as a site-relative path
    ("/car/detail-dr769ivm-zamyad-pickup-cng-1394"), so a link rendered straight
    from the column resolves against *our* origin and dead-ends inside the SPA.
    The Telegram notifier had its own copy of this fix and the website had none;
    one function, so a third caller cannot invent a fourth behaviour.
    """
    path = (path or "").strip()
    if not path:
        return ""
    return path if path.startswith("http") else urljoin(SITE_ROOT, path)


# ---------------------------------------------------------------------------
# Digits and integers
# ---------------------------------------------------------------------------

# Persian (Eastern Arabic) digits ۰..۹ -> ASCII 0..9.
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# Leading number only, so a range like "1399-1400" yields 1399 rather than the
# nonsense 13991400 a bare digit-strip produces.
_LEADING_NUMBER = re.compile(r"-?\d+")


def normalize_digits(value: str) -> str:
    return value.translate(_PERSIAN_DIGITS)


def parse_int(value: Any, *, positive: bool = False) -> int | None:
    """Best-effort int from Bama's free-form strings ("5,230,000,000 تومان").

    Returns None for empty/bool/unparseable input, and for non-positive results
    when ``positive`` is set.
    """
    if value is None or isinstance(value, bool):
        return None
    match = _LEADING_NUMBER.search(normalize_digits(str(value)).replace(",", ""))
    if not match:
        return None
    number = int(match.group())
    return number if not positive or number > 0 else None


# ---------------------------------------------------------------------------
# Model year and mileage
# ---------------------------------------------------------------------------
#
# Bama publishes `detail.year` in TWO calendars depending on brand: Iranian
# brands use Jalali ("1399"), imported brands Gregorian ("2025"), and 20+ brands
# use both across their own listings. Storing the raw number collapses 1399 and
# 2025 into one column and destroys every (model, variant, year) peer cohort, so
# the value is split into both calendars plus a tag naming which one it was.

# Iranian listing convention: Jalali 1404 <-> Gregorian 2025. The true offset
# drifts by one around Nowruz, but listings quote a flat delta.
JALALI_GREGORIAN_OFFSET = 621
JALALI_MIN, JALALI_MAX = 1300, 1420
GREGORIAN_MIN, GREGORIAN_MAX = 1900, 2100

# "صفر کیلومتر" (zero km) is the mileage string on ~33% of ads. "صفر" is the
# load-bearing token; the unit suffix varies.
_ZERO_KM_MARKER = "صفر"


def normalize_model_year(raw: Any) -> tuple[int | None, int | None, str]:
    """``(year_jalali, year_gregorian, calendar)``; calendar is jalali/gregorian/unknown.

    Anything outside both plausibility bands is unclassifiable, not a guess.
    """
    value = parse_int(raw)
    if value is None:
        return None, None, "unknown"
    if JALALI_MIN <= value <= JALALI_MAX:
        return value, value + JALALI_GREGORIAN_OFFSET, "jalali"
    if GREGORIAN_MIN <= value <= GREGORIAN_MAX:
        return value - JALALI_GREGORIAN_OFFSET, value, "gregorian"
    return None, None, "unknown"


def parse_mileage(raw: Any) -> int | None:
    """Kilometres, where ``0`` is a real value.

    ``parse_int`` returns None for "صفر کیلومتر", which would silently lose a
    *known* mileage of zero on a third of the corpus.
    """
    value = parse_int(raw)
    if value is None:
        return 0 if raw is not None and _ZERO_KM_MARKER in str(raw) else None
    return value if value >= 0 else None


# ---------------------------------------------------------------------------
# Flattening one payload
# ---------------------------------------------------------------------------


def extract_ad(payload: dict[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    """Flatten one Bama payload into the ad's query-friendly columns.

    Returns None when the payload carries no `detail.code`, which is the only
    thing that identifies an ad. Brand/model come out of the title (split on
    "،") when the payload does not name them.
    """
    detail = payload.get("detail") or {}
    code = detail.get("code")
    if not code:
        return None
    title_parts = [part.strip() for part in (detail.get("title") or "").split("،", 1)]
    price = payload.get("price") or {}
    return {
        "code": str(code),
        "title": detail.get("title"),
        "brand": detail.get("brand_fa") or (title_parts[0] if title_parts else None),
        "model": title_parts[1] if len(title_parts) > 1 else None,
        "trim": detail.get("trim"),
        "year": parse_int(detail.get("year"), positive=True),
        "mileage": parse_int(detail.get("mileage"), positive=True),
        "location": detail.get("location"),
        "body_type": detail.get("body_type_fa") or detail.get("body_type"),
        "body_color": detail.get("body_color"),
        "body_status": detail.get("body_status"),
        "fuel": detail.get("fuel"),
        "transmission": detail.get("transmission"),
        "category": detail.get("type"),
        "url": detail.get("url"),
        "publish_phrase": detail.get("time"),
        "current_price": parse_int(price.get("price"), positive=True),
        "current_payment": parse_int(price.get("payment"), positive=True),
        "current_prepayment": parse_int(price.get("prepayment"), positive=True),
        "current_installments": parse_int(price.get("installments"), positive=True),
        "price_type": price.get("type"),
        "last_seen_at": observed_at,
        "raw_payload": payload,
    }


# Keys the scraper adds for its own bookkeeping. Stripped so a stored payload
# stays byte-equal to what Bama actually sent.
_FORBIDDEN_AD_KEYS = ("computed_publish_date_jalali", "fetch_time_ts")


def pure_ad(ad: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in ad.items() if k not in _FORBIDDEN_AD_KEYS}


# ---------------------------------------------------------------------------
# Hashing / change detection
# ---------------------------------------------------------------------------
#
# Dotted paths dropped before the semantic hash: fields that move without the
# ad's content having changed. Measured across 1,125 consecutive version pairs,
# 81.2% differed ONLY in these, so four in five stored versions recorded nothing
# about the ad — and "a new version appeared" is the delta crawler's signal that
# a page is still moving. Observed change rate: detail.rank 100%, detail.time
# 73%, dealer.ad_count 49%, metadata.* 48%, detail.modified_date 26%,
# dealer.score 8%. Description (4.8%), images (3.0%) and price.* are real
# content and stay in.
VOLATILE_PAYLOAD_PATHS = (
    "detail.rank",
    "detail.time",
    "detail.modified_date",
    "dealer.ad_count",
    "dealer.score",
    "metadata",
)

# Bump whenever the paths above change: two semantic hashes are only comparable
# within one version, so without this a rule change silently splits an ad's
# history into two incomparable halves.
SEMANTIC_HASH_VERSION = 2


def fingerprint(value: Any) -> str:
    """sha256 over canonical (sorted-key, compact) JSON."""
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``payload`` with the volatile paths removed."""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    for path in VOLATILE_PAYLOAD_PATHS:
        parts = path.split(".")
        node = normalized
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return normalized


def payload_hashes(payload: dict[str, Any]) -> tuple[str, str]:
    """``(raw_hash, semantic_hash)``. The second decides "is this a new version"."""
    return fingerprint(payload), fingerprint(semantic_payload(payload))


# ---------------------------------------------------------------------------
# Listing identity across ad codes
# ---------------------------------------------------------------------------
#
# `semantic_hash` answers "did THIS ad change" and is scoped to one code. This
# answers "is this the same car as one we already saw", which is a different
# question: a seller who delists and relists gets a brand-new Bama code, and
# without this the pair reads as one removal plus one arrival — restarting the
# tenure clock and double-counting a delisting in every survival curve.
#
# Price is deliberately NOT part of the identity: relisting cheaper is the single
# most common reason to relist, so including it would miss exactly the cases
# worth catching. Nothing volatile is included either, for the same reason
# VOLATILE_PAYLOAD_PATHS exists.

# Collapse whitespace so a reformatted description is still the same text.
_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: Any) -> str:
    """Lowercased, digit-normalised, whitespace-collapsed."""
    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", normalize_digits(value).strip().lower())


def listing_fingerprint(*, brand: str | None, model: str | None, trim: str | None,
                        year: Any, mileage: Any, location: str | None,
                        body_color: str | None, description: str | None) -> str:
    """Content identity for one listing, stable across a repost.

    Returns ``""`` when the ad is too thin to identify — a blank fingerprint
    must never match another blank one, so callers store it and skip it rather
    than linking two unidentifiable ads together.
    """
    year_jalali, _, _ = normalize_model_year(year)
    km = parse_mileage(mileage)
    # Model and year are the minimum that makes "same car" meaningful. Mileage
    # is allowed to be absent (Bama omits it on some ads) but not the rest.
    if not model or year_jalali is None:
        return ""
    parts = [
        normalize_text(brand), normalize_text(model), normalize_text(trim),
        str(year_jalali), "" if km is None else str(km),
        normalize_text(location), normalize_text(body_color),
        normalize_text(description),
    ]
    return fingerprint(parts)


# ---------------------------------------------------------------------------
# Publish time
# ---------------------------------------------------------------------------

_JALALI_DATE = re.compile(r"(?P<year>1[34]\d{2})/(?P<month>\d{1,2})/(?P<day>\d{1,2})")
_RELATIVE_PHRASES = {
    "لحظاتی پیش": timedelta(),
    "دقایقی پیش": timedelta(minutes=5),
    "نیم ساعت پیش": timedelta(minutes=30),
    "یک ساعت پیش": timedelta(hours=1),
    "دیروز": timedelta(days=1),
    "پریروز": timedelta(days=2),
}
_RELATIVE_NUMERIC = re.compile(r"(\d+)\s+(دقیقه|ساعت|روز|هفته|ماه)\s+پیش")
_RELATIVE_UNITS = {
    "دقیقه": "minutes", "ساعت": "hours", "روز": "days",
    "هفته": "weeks", "ماه": "days",
}


def parse_absolute_jalali(value: str) -> datetime | None:
    """An absolute Jalali date phrase (YYYY/MM/DD) as UTC midnight."""
    match = _JALALI_DATE.search(normalize_digits(value.strip()))
    if not match:
        return None
    try:
        date = jdatetime.date(*(int(match.group(k)) for k in ("year", "month", "day")))
    except ValueError:
        return None
    return datetime.combine(date.togregorian(), datetime.min.time(), tzinfo=timezone.utc)


def parse_publish_time(value: str | None, observed_at: datetime) -> datetime | None:
    """Absolute date, then curated relative phrase, then numeric relative phrase.

    The order matters: an absolute date is unambiguous, a phrase is only
    meaningful relative to when it was observed.
    """
    if not value:
        return None
    absolute = parse_absolute_jalali(value)
    if absolute:
        return absolute
    text = normalize_digits(value.strip())
    if text in _RELATIVE_PHRASES:
        return observed_at - _RELATIVE_PHRASES[text]
    match = _RELATIVE_NUMERIC.fullmatch(text)
    if not match:
        return None
    number, unit = int(match.group(1)), match.group(2)
    # A month is 30 days here; the feed only ever means "roughly that long ago".
    return observed_at - timedelta(**{_RELATIVE_UNITS[unit]: number * (30 if unit == "ماه" else 1)})
