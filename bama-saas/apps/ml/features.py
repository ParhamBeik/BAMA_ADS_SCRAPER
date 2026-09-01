"""The design matrix: which columns the models see, and how a row becomes one.

The current scorer reads nine columns and groups on three of them. Everything
below is already stored on ``Ad`` and read by nothing — body type, fuel,
transmission, city, whether a dealer posted it, how many photos it carries, how
long the description is, whether the seller is verified. That is the actual
argument for a learned layer here: not that gradient boosting is cleverer than a
median, but that a median over a (model, variant, year) key structurally cannot
use a column that is not in the key, and eleven of these are not.

Two rules this module exists to enforce:

**One definition of a feature row, shared by training and inference.** The
classic way a model that scored well offline scores badly in production is that
the two paths built their columns slightly differently — a different fill for a
null, a category encoded in a different order. There is one ``build`` here and
both callers use it, with the vocabularies pinned in ``FeatureSpec`` and stored
on the ``MLModel`` row.

**Unseen categories are a value, not an error.** A brand new trim appears on
Bama every week. It encodes to ``UNSEEN`` (-1), which LightGBM handles as its own
category, rather than raising or silently landing in whatever bucket happened to
be zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from apps.core.quality import CONDITION_BANDS, condition_band

# LightGBM's convention for "not one of the categories seen at fit time". Kept
# explicit because the alternative — letting an unknown trim collapse into
# category 0, which is some real trim — is a silent, plausible wrong answer.
UNSEEN = -1

# Ordinal, not one-hot, and the order is the measured haircut ladder from
# `pricing.condition_haircuts`: clean -> cosmetic -> painted -> structural.
# A tree can split an ordinal anywhere, so nothing is lost, and the model gets
# the monotone relationship for free instead of having to rediscover it from
# four independent indicators.
CONDITION_ORDINAL = {band: i for i, band in enumerate(CONDITION_BANDS)}

# The columns, in order. This tuple *is* the contract — `FeatureSpec.columns`
# is a copy of it taken at fit time, and inference refuses a spec that does not
# match rather than silently feeding column 7 into slot 8.
NUMERIC_COLUMNS = (
    "mileage",
    "log_mileage",
    "year_jalali",
    "age_years",
    "condition_ordinal",
    "days_listed",
    "image_count",
    "description_length",
    "seller_authenticated",
    "is_dealer",
)
CATEGORICAL_COLUMNS = (
    "brand_id",
    "model_id",
    "variant_id",
    "city_id",
    "body_type",
    "fuel",
    "transmission",
)
COLUMNS = NUMERIC_COLUMNS + CATEGORICAL_COLUMNS

# The `.values()` keys `build` needs. Named once so a caller cannot fetch a
# subset and get silent nulls for the rest.
QUERY_FIELDS = (
    "code", "brand_id", "model_id", "variant_id", "city_id", "dealer_id",
    "current_price", "mileage", "year_jalali", "body_status", "body_type",
    "fuel", "transmission", "image_count", "description_length",
    "seller_authenticated", "publish_at", "first_seen_at",
)

# Jalali "now". Age is computed against the current Jalali year rather than
# against a Gregorian one: `year_jalali` is a Jalali number, and subtracting it
# from 2026 would make every car 600 years old.
JALALI_EPOCH_OFFSET = 621


def current_jalali_year(now: datetime | None = None) -> int:
    """The Jalali year, near enough for an age in whole years.

    Deliberately not `jdatetime`: this is an integer feature that a tree splits
    into bins, so being one day out around Nowruz cannot change a prediction,
    and an exact conversion here would be precision the model cannot use.
    """
    return (now or timezone.now()).year - JALALI_EPOCH_OFFSET


@dataclass
class FeatureSpec:
    """Everything needed to rebuild an identical matrix later.

    Stored as JSON on the ``MLModel`` row. `vocabularies` maps a categorical
    column to ``{raw value: code}``; anything absent encodes to ``UNSEEN``.
    """

    columns: tuple[str, ...] = COLUMNS
    categorical: tuple[str, ...] = CATEGORICAL_COLUMNS
    vocabularies: dict[str, dict] = field(default_factory=dict)
    jalali_year: int = 0

    def to_json(self) -> dict:
        # JSON object keys are strings whatever we put in them, so ids go in as
        # strings deliberately — round-tripping an int key through JSON and back
        # would otherwise turn `{12: 3}` into `{"12": 3}` and every id would
        # miss its own vocabulary entry on the way back in.
        return {
            "columns": list(self.columns),
            "categorical": list(self.categorical),
            "vocabularies": {
                col: {str(k): v for k, v in vocab.items()}
                for col, vocab in self.vocabularies.items()
            },
            "jalali_year": self.jalali_year,
        }

    @classmethod
    def from_json(cls, blob: dict) -> FeatureSpec:
        return cls(
            columns=tuple(blob.get("columns") or COLUMNS),
            categorical=tuple(blob.get("categorical") or CATEGORICAL_COLUMNS),
            vocabularies={
                col: dict(vocab) for col, vocab in (blob.get("vocabularies") or {}).items()
            },
            jalali_year=int(blob.get("jalali_year") or 0),
        )

    @property
    def categorical_indices(self) -> list[int]:
        """Positions LightGBM should treat as categorical rather than ordered."""
        return [i for i, c in enumerate(self.columns) if c in set(self.categorical)]


def fit_spec(rows: list[dict], *, now: datetime | None = None) -> FeatureSpec:
    """Learn the categorical vocabularies from the training rows only.

    From the *training* rows, never the whole table: a vocabulary that includes
    categories only present after the split date is a small leak of the future
    into the fit, and the interval-coverage number is exactly the kind of metric
    that leak would flatter.
    """
    vocabularies: dict[str, dict] = {}
    for col in CATEGORICAL_COLUMNS:
        seen = sorted({_raw(r, col) for r in rows if _raw(r, col) not in (None, "")},
                      key=str)
        vocabularies[col] = {value: code for code, value in enumerate(seen)}
    return FeatureSpec(vocabularies=vocabularies, jalali_year=current_jalali_year(now))


def _raw(row: dict, col: str):
    """The un-encoded value of a categorical column for one row."""
    return row.get(col)


def _f(value, default: float = float("nan")) -> float:
    """A float, with NaN — not zero — for missing.

    LightGBM routes NaN down its own branch at every split, which is a *learned*
    treatment of missingness. Filling with 0 instead would tell the model that a
    car with no recorded mileage has driven zero kilometres, and ~33% of this
    catalogue genuinely has driven zero kilometres, so those two would be
    indistinguishable.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def row_features(row: dict, spec: FeatureSpec, *, now: datetime | None = None) -> list[float]:
    """One ad row -> one feature vector, in ``spec.columns`` order."""
    now = now or timezone.now()
    mileage = row.get("mileage")
    band = condition_band(row.get("body_status") or "")
    published = row.get("publish_at") or row.get("first_seen_at")
    days_listed = (now - published).days if published else None
    jalali_now = spec.jalali_year or current_jalali_year(now)
    year = row.get("year_jalali")

    values = {
        "mileage": _f(mileage),
        # Prices and mileages are both heavily right-skewed and a tree splits on
        # rank, so the log is not there to help the tree — it is there because
        # the *linear* pieces downstream (the residual, the anomaly space) are
        # scale-sensitive and 12,000km vs 300,000km should not be one axis of
        # raw magnitude.
        "log_mileage": math.log1p(mileage) if mileage is not None and mileage >= 0
        else float("nan"),
        "year_jalali": _f(year),
        "age_years": float(jalali_now - year) if year else float("nan"),
        "condition_ordinal": (
            float(CONDITION_ORDINAL[band]) if band in CONDITION_ORDINAL else float("nan")
        ),
        "days_listed": _f(days_listed),
        "image_count": _f(row.get("image_count")),
        "description_length": _f(row.get("description_length")),
        # Tri-state on purpose: True, False and "Bama did not say" are three
        # different things and the third is common.
        "seller_authenticated": (
            float(row["seller_authenticated"])
            if row.get("seller_authenticated") is not None else float("nan")
        ),
        # `AdFilter` already derives seller type this way — a dealer_id is what
        # a dealership listing has and a private sale does not.
        "is_dealer": 1.0 if row.get("dealer_id") else 0.0,
    }
    for col in spec.categorical:
        vocab = spec.vocabularies.get(col) or {}
        raw = _raw(row, col)
        # The vocabulary round-trips through JSON, where every key is a string.
        values[col] = float(vocab.get(str(raw), vocab.get(raw, UNSEEN)))
    return [values[c] for c in spec.columns]


def build(rows: list[dict], spec: FeatureSpec, *, now: datetime | None = None):
    """``(X, codes)`` — the matrix and the ad code for each of its rows.

    numpy is imported here rather than at module scope so this module can be
    imported (and the constants above read) on a host without the ``ml`` extra.
    """
    import numpy as np

    if not rows:
        return np.zeros((0, len(spec.columns)), dtype=np.float64), []
    matrix = np.array([row_features(r, spec, now=now) for r in rows], dtype=np.float64)
    return matrix, [r["code"] for r in rows]


def text_of(row: dict) -> str:
    """The text the classifier reads for one ad.

    Title plus trim, not the description: the description is where sellers write
    prose about tyres and service history, and a classifier trained on it learns
    which dealership wrote the ad rather than which car it is about. Both are
    normalised through `apps.core.normalization`, which already handles the ZWNJ
    and Arabic/Persian character folding that would otherwise make «پژو» and
    «پژو» two different tokens.
    """
    from apps.core.normalization import normalize_text

    return normalize_text(" ".join(
        part for part in (row.get("title"), row.get("trim")) if part
    ))
