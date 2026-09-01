"""Where artifacts live, how a model becomes live, and how it stops being live.

The registry is deliberately boring: joblib files on a shared volume, a row per
file, and a status column. There is no model server, no artifact store and no
experiment tracker, because the deployment question this app actually has to
answer — "which model produced the number on this card, and can I put the old
one back" — is answered by a foreign key and one UPDATE.

The one rule worth stating out loud: **promotion is a decision with a recorded
reason, not a side effect of training.** ``promote`` refuses unless the
challenger beat both the incumbent and the statistical baseline on the same
holdout, and it writes the comparison into ``metrics["promotion"]`` either way.
A model held in shadow because it lost is a result; a model that quietly became
live because it was the newest is an accident waiting to be discovered.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.ml.models import MLModel

logger = logging.getLogger("bama.ml")

# Whether the learned layer can run at all here. Checked rather than assumed
# because `ml` is its own extra: a host that installed the web app without it
# should degrade to "no prediction available", which is a refusal this codebase
# already knows how to render, and not to a 500 from an import at module scope.
try:  # pragma: no cover - trivially one branch or the other per environment
    import joblib  # noqa: F401
    import lightgbm  # noqa: F401
    import numpy  # noqa: F401
    import sklearn  # noqa: F401

    ML_AVAILABLE = True
    ML_UNAVAILABLE_REASON = ""
except ImportError as exc:  # pragma: no cover
    ML_AVAILABLE = False
    ML_UNAVAILABLE_REASON = f"ml extra not installed: {exc}"


def jsonable(value):
    """Plain Python, recursively — numpy scalars are not JSON.

    Every metric here starts life inside numpy, and ``np.float64`` /
    ``np.bool_`` survive ``round()`` and ``and``/``or`` while failing
    ``json.dumps`` with "Object of type bool is not JSON serializable". That
    error surfaces at the ``save()``, several frames from the arithmetic that
    caused it, which is why the coercion lives at this one choke point rather
    than being sprinkled through the trainers: every value on its way into a
    JSONField goes through here, so a new trainer cannot reintroduce it.
    """
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    # `bool` before the number check: bool is a subclass of int and would
    # otherwise be written as 0/1.
    if isinstance(value, bool):
        return bool(value)
    item = getattr(value, "item", None)  # numpy scalars expose .item()
    if item is not None and type(value).__module__ == "numpy":
        return jsonable(item())
    if isinstance(value, float):
        # NaN and infinity are valid Python floats and invalid JSON. They reach
        # here whenever a metric was computed over an empty slice.
        return None if value != value or value in (float("inf"), float("-inf")) else value
    return value


def artifact_dir() -> Path:
    """The directory artifacts are written to, created on demand.

    A setting rather than a constant because the training container writes here
    and the web container mounts the same volume read-only; they need to agree
    on the path and disagree on the permissions.
    """
    path = Path(getattr(settings, "ML_ARTIFACT_DIR", settings.BASE_DIR / "data" / "ml"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_version(name: str) -> int:
    latest = MLModel.objects.filter(name=name).order_by("-version").values_list(
        "version", flat=True).first()
    return (latest or 0) + 1


def save(name: str, version: int, payload) -> str:
    """Persist one artifact and return its path.

    ``compress=3`` because these files cross a docker volume and a LightGBM
    booster pickles large; the decompression cost is paid once per process at
    load, against a file read on every container start.
    """
    import joblib

    path = artifact_dir() / f"{name}_v{version}.joblib"
    joblib.dump(payload, path, compress=3)
    return str(path)


def load(record: MLModel):
    """The artifact behind one registry row, or ``None`` if it is gone.

    Missing is a real state, not an exception: the volume can be recreated
    empty, and the correct behaviour then is for inference to refuse — the same
    refusal it already gives for a cohort that is too thin — rather than for the
    process to fail to start.
    """
    import joblib

    if not record.artifact_path:
        return None
    path = Path(record.artifact_path)
    if not path.exists():
        logger.warning("ml artifact missing for %s: %s", record, path)
        return None
    return joblib.load(path)


def active(name: str) -> MLModel | None:
    return MLModel.objects.filter(name=name, status=MLModel.Status.ACTIVE).first()


# Parts of a stored feature spec that describe *this fit* rather than *the
# task*. `FeatureSpec.to_json` pins the fitted category vocabularies and the
# Jalali year it was built against, and both move every night as new brands and
# cities arrive. Comparing them would make every retrain look like a different
# task and quietly switch off the incumbent half of the promotion gate — which
# is exactly what the first version of this check did to four of the five
# models. What defines the task is which columns go in, not what was in them.
FITTED_SPEC_KEYS = ("vocabularies", "jalali_year")


def task_signature(spec: dict | None) -> dict:
    """The part of a feature spec that says what problem is being solved."""
    return {k: v for k, v in (spec or {}).items() if k not in FITTED_SPEC_KEYS}


def incumbent_metric(name: str, key: str, *, feature_spec: dict | None = None) -> float | None:
    """The live model's score on one metric, for the gate to beat.

    ``feature_spec`` makes the comparison refuse itself when the challenger no
    longer reads the same inputs. Two macro-F1 numbers are only rankable if they
    measure the same task, and changing what goes into the vectoriser changes
    the task — the model/text classifier scored 1.0 while the ad title still
    contained the label it was predicting, and no honest successor reading brand
    and trim alone can ever beat that. Returning ``None`` here makes the gate
    treat the incumbent as "nothing to beat" and judge the challenger on the
    baseline and its own merits, which is the only sound thing to do when the
    two numbers are not the same quantity.
    """
    current = active(name)
    if current is None:
        return None
    if (feature_spec is not None
            and task_signature(current.feature_spec) != task_signature(feature_spec)):
        logger.info("ml.incumbent_incomparable name=%s reason=feature_spec_changed", name)
        return None
    value = (current.metrics or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


@transaction.atomic
def promote(record: MLModel, *, decision: dict) -> bool:
    """Make ``record`` the active model for its name, if the gate says so.

    Returns whether it was promoted. Either way the decision — the challenger's
    score, the incumbent's, the baseline's, and the verdict — is written to the
    row, so "why is the old model still live?" is a column read and not an
    excavation of the training log.

    Retiring the incumbent and activating the challenger happen in one
    transaction against a partial unique index on (name, status=active), so
    there is no instant at which two models for one role are both live and no
    instant at which none is.
    """
    record.metrics = jsonable({**(record.metrics or {}), "promotion": decision})
    if not decision.get("promote"):
        record.status = MLModel.Status.SHADOW
        record.save(update_fields=["metrics", "status"])
        logger.info("ml %s v%s held in shadow: %s", record.name, record.version,
                    decision.get("reason"))
        return False

    MLModel.objects.filter(name=record.name, status=MLModel.Status.ACTIVE).exclude(
        pk=record.pk).update(status=MLModel.Status.RETIRED)
    record.status = MLModel.Status.ACTIVE
    record.save(update_fields=["metrics", "status"])
    logger.info("ml %s v%s promoted: %s", record.name, record.version,
                decision.get("reason"))
    return True


def gate(*, challenger: float | None, incumbent: float | None, baseline: float | None,
         lower_is_better: bool = True, margin: float = 0.0,
         veto: tuple[bool, str] | None = None) -> dict:
    """The promotion decision, as data.

    A challenger must beat **both** the model it would replace and the
    statistical baseline it sits beside. Beating only the incumbent is how a
    line of models drifts away from something simpler that was always better;
    `apps/core/pricing.py` records the last time a fitted model shipped here
    without that check, and the peer median has been the number on the card ever
    since.

    ``margin`` is the improvement required to bother swapping — a challenger
    that is 0.2% better is noise, and every swap invalidates a cache and changes
    numbers a reader may have screenshotted.

    ``veto`` is a second, non-negotiable condition that is not an error metric.
    It exists because the price model is judged on two different things: whether
    its point estimate is accurate, and whether the *band* it draws around that
    estimate is honest. A model can win the first comfortably while its p10..p90
    contains 43% of held-out cars instead of 80% — which is a model that looks
    precise and is not, and no accuracy score can see it. Anything vetoed is
    reported with its own reason rather than with a comparison it actually won.
    """
    if veto is not None and veto[0]:
        return {"promote": False, "reason": veto[1], "vetoed": True,
                "challenger": challenger, "incumbent": incumbent, "baseline": baseline}
    if challenger is None:
        return {"promote": False, "reason": "no_challenger_metric",
                "challenger": None, "incumbent": incumbent, "baseline": baseline}

    def beats(other: float | None) -> bool:
        if other is None:
            return True  # nothing to beat is not a reason to refuse
        return (challenger < other * (1 - margin) if lower_is_better
                else challenger > other * (1 + margin))

    beat_incumbent, beat_baseline = beats(incumbent), beats(baseline)
    reason = (
        "beats_incumbent_and_baseline" if beat_incumbent and beat_baseline
        else "loses_to_baseline" if beat_incumbent
        else "loses_to_incumbent" if beat_baseline
        else "loses_to_both"
    )
    return {
        "promote": beat_incumbent and beat_baseline,
        "reason": reason,
        "challenger": challenger,
        "incumbent": incumbent,
        "baseline": baseline,
        "lower_is_better": lower_is_better,
        "margin": margin,
    }


def register(*, name: str, algorithm: str, payload, metrics: dict, feature_spec: dict,
             training_rows: int, trained_through=None, notes: str = "") -> MLModel:
    """Write one trained artifact and its row. Always SHADOW at first."""
    version = next_version(name)
    path = save(name, version, payload)
    return MLModel.objects.create(
        name=name, version=version, algorithm=algorithm,
        status=MLModel.Status.SHADOW, artifact_path=path,
        metrics=jsonable(metrics), feature_spec=jsonable(feature_spec),
        training_rows=training_rows, trained_through=trained_through, notes=notes,
    )
