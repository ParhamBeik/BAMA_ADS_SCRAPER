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
from django.utils import timezone

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
    value = _dig(current.metrics or {}, key)
    if value is None:
        # Loud, because the silent version of this cost the anomaly gate its
        # entire incumbent half: `lift` lives at `precision_at_k.lift`, the
        # lookup asked for a top-level `lift`, and a missing metric reads as
        # "no incumbent to beat" — which is indistinguishable from a first
        # training run and so never looked wrong. Production 2026-09-07: every
        # anomaly decision ever made carried `incumbent_basis=no_incumbent`
        # while a model was demonstrably active.
        logger.warning("ml.incumbent_metric_missing name=%s key=%s", name, key)
    return value


def _dig(blob: dict, key: str) -> float | None:
    """A metric by dotted path, so a nested one can still decide a promotion.

    ``precision_at_k.lift`` rather than ``lift``. Trainers group related numbers
    into sub-dicts for readability and the gate has to be able to name one of
    them; a flat lookup silently returned ``None`` instead.
    """
    node = blob
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return float(node) if isinstance(node, (int, float)) else None


def incumbent_context(name: str, key: str, *, feature_spec: dict | None = None) -> dict:
    """Everything ``gate`` needs about the model currently serving this role.

    Splatted into the gate as ``**incumbent_context(...)``, so a trainer states
    which metric decides its role and nothing else. The extra two fields are
    what let the gate tell "the challenger is worse" apart from "the challenger
    sat a harder exam" — see ``gate``.

    The baseline is read from the incumbent's own promotion decision rather than
    recomputed: it is the number that was measured on the incumbent's holdout,
    and recomputing it today would reintroduce the very mismatch this fixes.
    """
    current = active(name)
    if current is None:
        return {"incumbent": None, "incumbent_baseline": None,
                "incumbent_age_days": None}
    if (feature_spec is not None
            and task_signature(current.feature_spec) != task_signature(feature_spec)):
        return {"incumbent": None, "incumbent_baseline": None,
                "incumbent_age_days": None}
    score = incumbent_metric(name, key, feature_spec=feature_spec)
    if score is None:
        # Live row, readable task, unreadable number. Treating that as
        # "nothing to beat" is how a missing key promoted freely.
        return {"incumbent": None, "incumbent_baseline": None,
                "incumbent_age_days": None, "incumbent_unreadable": True}
    promotion = (current.metrics or {}).get("promotion") or {}
    baseline = promotion.get("baseline")
    age = None
    if current.trained_at:
        age = (timezone.now() - current.trained_at).total_seconds() / 86400.0
    return {
        "incumbent": score,
        "incumbent_baseline": float(baseline) if isinstance(baseline, (int, float)) else None,
        "incumbent_age_days": age,
    }


def incumbent_artifact(name: str, *, feature_spec: dict | None = None):
    """The live model and its loaded artifact, for re-scoring on a fresh holdout.

    The counterpart to ``incumbent_context``: that one reads the incumbent's
    stored number, this one hands back the thing itself so a trainer can make it
    sit the challenger's exam. Returns ``(None, None)`` whenever the comparison
    would be dishonest or impossible — no incumbent, a changed task signature, or
    an artifact the volume no longer has — and every one of those is a normal
    state that must leave the gate treating the incumbent as "nothing to beat"
    rather than crashing the nightly train.

    The artifact carries its *own* feature spec and its own conformal delta, and
    a caller must use them rather than the challenger's: re-scoring a model with
    somebody else's preprocessing measures neither model.
    """
    current = active(name)
    if current is None:
        return None, None
    if (feature_spec is not None
            and task_signature(current.feature_spec) != task_signature(feature_spec)):
        logger.info("ml.incumbent_incomparable name=%s reason=feature_spec_changed", name)
        return None, None
    try:
        artifact = load(current)
    except Exception as exc:  # noqa: BLE001 — a stale pickle must not stop training
        logger.warning("ml.incumbent_artifact_unloadable name=%s error=%r", name, exc)
        return None, None
    if artifact is None:
        return None, None
    return current, artifact


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


# How long an incumbent may sit unchallengeable before a tie stops going its way.
#
# It is the correction that unsticks a line of models entirely: `model_text` and
# `value_tier` both sat on versions from 2026-09-01 while fifteen challengers
# within ~1% of them were refused, one a night, because "within 1%" reads as
# "loses" to a strict bar.
#
# This used to be an `elif` *after* the baseline-normalised branch, which made it
# unreachable for any model that recorded a baseline on both sides — i.e. every
# model that most needed it. `price` sat on v10 for five days with 21 refusals
# while holding an `incumbent_age_days` of 4.9 that nothing ever read. Staleness
# is now applied *within* whichever comparison basis was chosen, because how old
# the incumbent is and how the two scores are made comparable are two independent
# questions and collapsing them into one if/elif chain answered only the second.
INCUMBENT_STALE_AFTER_DAYS = 3.0


def _usable(value: float | None) -> bool:
    """A number the gate can divide by."""
    return isinstance(value, (int, float)) and value not in (0, None)


def gate(*, challenger: float | None, incumbent: float | None, baseline: float | None,
         lower_is_better: bool = True, margin: float = 0.0,
         veto: tuple[bool, str] | None = None,
         incumbent_baseline: float | None = None,
         incumbent_age_days: float | None = None,
         incumbent_rescored: bool = False,
         incumbent_unreadable: bool = False) -> dict:
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

    ``incumbent_baseline`` and ``incumbent_age_days`` describe the *incumbent's
    own* evaluation, and they exist because comparing a challenger's fresh score
    against an incumbent's stored one is comparing two different exams.

    Every trainer here splits on time, so each night's holdout is a different
    slice of a moving market. The baseline is recomputed on the current holdout
    — that comparison was always sound — but the incumbent's number is read
    frozen off the row it was written on, which silently asks the challenger to
    beat a score earned on older, easier data. Production on 2026-09-06: four of
    five models had been refused every night for four days, each losing by
    0.6%-4%, while the *baseline* they were measured against had itself drifted
    (price: 0.0406 -> 0.0437, an 8% harder holdout). Nothing was regressing; the
    exam had got harder and only one side was re-sat.

    ``incumbent_rescored`` says the caller did the honest thing instead: loaded
    the incumbent's artifact and ran it on *this* holdout, so the two scores are
    the same exam literally rather than by approximation. When it is set the
    numbers are compared directly, because there is nothing left to correct for.

    Otherwise, when both sides recorded a baseline they are compared as *lift
    over their own baseline*, which cancels holdout difficulty only to first
    order and assumes that ratio is itself stable — good enough to unstick a
    gate, not good enough to trust when it is the thing deciding. Failing that,
    the raw scores are compared and the mismatch is simply accepted.

    Staleness is then applied on top of whichever basis was chosen: past
    ``INCUMBENT_STALE_AFTER_DAYS`` a tie goes to the fresher model. It used to be
    a fourth mutually-exclusive branch below the normalised one, which made it
    dead code for exactly the models it was written for — see the constant.

    The baseline half of the gate is untouched — it is measured on the
    challenger's own holdout and was never the problem.
    """
    if veto is not None and veto[0]:
        return {"promote": False, "reason": veto[1], "vetoed": True,
                "challenger": challenger, "incumbent": incumbent, "baseline": baseline}
    if incumbent_unreadable:
        return {"promote": False, "reason": "incumbent_metric_missing",
                "challenger": challenger, "incumbent": incumbent, "baseline": baseline,
                "incumbent_unreadable": True}
    if challenger is None:
        return {"promote": False, "reason": "no_challenger_metric",
                "challenger": None, "incumbent": incumbent, "baseline": baseline}

    def beats(mine: float, other: float | None) -> bool:
        if other is None:
            return True  # nothing to beat is not a reason to refuse
        return (mine < other * (1 - margin) if lower_is_better
                else mine > other * (1 + margin))

    def not_worse_than(mine: float, other: float) -> bool:
        """A tie counts. Used only where the two numbers are not comparable."""
        return (mine <= other * (1 + margin) if lower_is_better
                else mine >= other * (1 - margin))

    # How the incumbent half was decided, recorded so a refusal is auditable.
    #
    # Two independent questions, answered in order: *what pair of numbers is
    # comparable at all* (the basis), and *how hard must the challenger beat
    # them* (strict, or a tie if the incumbent has gone stale).
    if incumbent is None:
        basis = "no_incumbent"
        beat_incumbent = True
    else:
        if incumbent_rescored:
            # The incumbent was re-run on this holdout, so the two numbers are
            # the same exam literally. Nothing to normalise and nothing to
            # approximate — this is what `baseline_normalised` was a proxy for.
            basis = "rescored_same_holdout"
            mine, theirs = challenger, incumbent
        elif _usable(baseline) and _usable(incumbent_baseline):
            # Same exam approximated: each model's lift over the baseline it was
            # actually measured against, which cancels holdout difficulty to
            # first order but assumes that ratio is itself stable.
            basis = "baseline_normalised"
            mine, theirs = challenger / baseline, incumbent / incumbent_baseline
        else:
            basis = "raw"
            mine, theirs = challenger, incumbent

        if incumbent_age_days is not None and incumbent_age_days > INCUMBENT_STALE_AFTER_DAYS:
            basis += "+stale_tie_breaks_to_fresh"
            beat_incumbent = not_worse_than(mine, theirs)
        else:
            beat_incumbent = beats(mine, theirs)

    beat_baseline = beats(challenger, baseline)
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
        "incumbent_baseline": incumbent_baseline,
        "incumbent_age_days": incumbent_age_days,
        "incumbent_basis": basis,
        "incumbent_rescored": incumbent_rescored,
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
