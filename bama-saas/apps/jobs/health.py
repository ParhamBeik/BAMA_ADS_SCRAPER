"""Crawl health: eleven checks over what the crawl already records.

Every failure mode here was already *detectable* — FetchRun stores status and
stop_reason, PageCoverage stores which ranks were read, IngestReject stores
every quarantined payload with its rule. What was missing was anything that
reads them: the documented answer to "did Bama change their schema?" was a SQL
query an operator was expected to remember to run, which means in practice the
crawler could rot for days behind a green-looking API.

So this adds no schema and no crawl load — pure queries. Severity is binary on
purpose: a check is either OK or it needs a human. Graded severities invite
"warning" states that are quietly tolerated forever.

Split out of ``jobs.py``, which was 1,625 lines of two unrelated things: the
scheduled jobs themselves and this. ``pipeline.JOBS["health"]`` still routes to
``health`` here — that table is the only inbound reference most of these have.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from apps.core.coverage import (
    COVERAGE_GAP_TOLERANCE_RANKS,
    COVERAGE_WINDOW_HOURS,
    coverage_state,
)
from apps.core.models import Ad, FetchRun, IngestReject, JobRun, NotifierSettings
from apps.core.notify import send_health_alert as deliver_health_alert
from apps.jobs.jobs import REQUIRED_MISSED_WINDOWS, sweep_cutoff

logger = logging.getLogger("bama.jobs")

#
# Every failure mode below was already *detectable* — FetchRun stores status and
# stop_reason, PageCoverage stores which ranks were read, IngestReject stores
# every quarantined payload with its rule. What was missing was anything that
# reads them: the documented answer to "did Bama change their schema?" was a SQL
# query an operator was expected to remember to run, which means in practice the
# crawler could rot for days behind a green-looking API.
#
# So this adds no schema and no crawl load — pure queries. Severity is binary on
# purpose: a check is either OK or it needs a human. Graded severities invite
# "warning" states that are quietly tolerated forever.

RECENT_HOURS = 24.0

# A single rule firing this many times its 7-day baseline means Bama changed
# something. Below REJECT_SPIKE_MIN_COUNT, 3x of two rejects is not a signal.
REJECT_SPIKE_FACTOR = 3.0
REJECT_SPIKE_MIN_COUNT = 20


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # The numbers behind the verdict, so a caller needs no re-query.
    data: dict = field(default_factory=dict)


def check_source_block(now=None) -> Check:
    """Is bama.ir refusing us, and how long until the next attempt?

    Its own check because the operator response is completely different from a
    normal failure: nothing in this codebase can fix a 403 from the source's CDN.
    """
    from apps.core.coverage import consecutive_blocks
    from apps.jobs.fetcher import cooldown_until

    now = now or timezone.now()
    streak = consecutive_blocks()
    until = cooldown_until()
    if not streak:
        return Check("source_block", True, "bama.ir is answering; no active block.")
    mins = max(0.0, ((until - now).total_seconds() / 60) if until else 0.0)
    return Check(
        "source_block", False,
        f"bama.ir returned 403 on {streak} consecutive run(s). Next attempt in "
        f"{mins:.0f} min. The catalog is frozen until it clears; removal "
        f"detection stays paused.",
        {"consecutive_blocks": streak,
         "next_attempt_at": until.isoformat() if until else None},
    )


def check_sweep_freshness(now=None) -> Check:
    """Was the whole feed covered in the last window?

    Coverage accumulates across runs, so a feed can be fully covered by several
    partial sweeps with no run setting ``reached_end`` at all — asking for that
    flag reported permanent failure while the crawler worked correctly.

    Uses the same slack as ``coverage_is_complete`` and removal detection: up
    to one page of uncovered ranks is normal on a live feed and must not read
    as a failed sweep while removal detection is green.
    """
    now = now or timezone.now()
    state = coverage_state(now)
    if not state.depth:
        return Check(
            "sweep_freshness", False,
            "No pages fetched in the depth window, so feed depth is unknown. "
            "Nothing can be proven about coverage and removal detection stays disabled.",
        )
    depth, gaps, missing, complete = (
        state.depth, state.gaps, state.missing, state.complete)
    detail = (
        f"Feed fully covered in the last {COVERAGE_WINDOW_HOURS:.0f}h "
        f"(ceiling {depth}, ≤{COVERAGE_GAP_TOLERANCE_RANKS} rank slack)."
        if complete else
        f"{len(gaps)} uncovered rank range(s) (~{missing} ad slots) in the last "
        f"{COVERAGE_WINDOW_HOURS:.0f}h; removal detection is paused until closed."
    )
    return Check("sweep_freshness", complete, detail,
                 {"feed_depth": depth, "gap_count": len(gaps), "missing_ranks": missing,
                  "tolerance_ranks": COVERAGE_GAP_TOLERANCE_RANKS})


def check_failed_runs(now=None) -> Check:
    """Any FAILED fetch in the window — except a WAF block, which has its own check.

    Blocked runs used to land here, and during the 2026-08-16 block this read
    "245 failed run(s)" for a situation with exactly one cause, burying every
    other signal on the page.
    """
    now = now or timezone.now()
    failed = FetchRun.objects.filter(
        started_at__gte=now - timedelta(hours=RECENT_HOURS), status=FetchRun.Status.FAILED
    ).exclude(stop_reason=FetchRun.StopReason.BLOCKED)
    rows = list(failed.order_by("-started_at").values_list("mode", "error")[:5])
    total = failed.count()
    if not total:
        return Check("failed_runs", True, f"No failed runs in {RECENT_HOURS:.0f}h.")
    sample = "; ".join(f"{mode}: {(err or '')[:120]}" for mode, err in rows)
    return Check("failed_runs", False,
                 f"{total} failed run(s) in {RECENT_HOURS:.0f}h. {sample}", {"count": total})


def check_reject_spike(now=None) -> Check:
    """A jump in one rule id is how a Bama schema change announces itself."""
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    baseline_since = now - timedelta(days=7)

    recent = dict(
        IngestReject.objects.filter(observed_at__gte=since)
        .values_list("rule").annotate(n=Count("id"))
    )
    if not recent:
        return Check("reject_spike", True, f"No rejects in {RECENT_HOURS:.0f}h.")

    baseline = dict(
        IngestReject.objects
        .filter(observed_at__gte=baseline_since, observed_at__lt=since)
        .values_list("rule").annotate(n=Count("id"))
    )
    # Baseline is ~6 days of history vs a 1-day window; compare like with like.
    baseline_days = max(1.0, (since - baseline_since).total_seconds() / 86400.0)
    window_days = RECENT_HOURS / 24.0

    spikes = []
    for rule, count in recent.items():
        if count < REJECT_SPIKE_MIN_COUNT:
            continue
        expected = (baseline.get(rule, 0) / baseline_days) * window_days
        # An unseen rule firing at volume is the strongest possible signal.
        if expected == 0 or count >= expected * REJECT_SPIKE_FACTOR:
            spikes.append((rule, count, round(expected, 1)))

    if not spikes:
        return Check("reject_spike", True,
                     f"{sum(recent.values())} reject(s) in {RECENT_HOURS:.0f}h, all "
                     f"within {REJECT_SPIKE_FACTOR:g}x baseline.")
    detail = "; ".join(f"{rule}: {count} vs {exp} expected" for rule, count, exp in spikes)
    return Check("reject_spike", False,
                 f"Ingest reject spike — Bama likely changed their payload. {detail}",
                 {"spikes": [{"rule": r, "count": c, "expected": e} for r, c, e in spikes]})


# Coverage runs on a ~10 minute cadence, so a couple of hours of silence is
# twelve missed turns — far past anything a slow sweep explains, and short
# enough to notice inside one working day.
COVERAGE_STARVED_AFTER = timedelta(hours=2)


def check_coverage_progress(now=None) -> Check:
    """Is rolling coverage still getting a turn on the worker?

    Its own check because coverage failing *silently* is the specific way
    removal detection dies. There is no full sweep any more — coverage
    accumulates from bounded chunks — so a coverage job that stops running
    leaves the deep tail unread, the feed never reads as fully covered, and
    `mark_inactive` refuses to mark anything. Every symptom of that shows up on
    `check_sweep_freshness` as uncovered ranges, with nothing saying why.

    Watches for *absence*, not for failure. Coverage shares the fetch lock with
    the hot tick (`deploy/worker.sh`), and a cadence that loses that race never
    reaches Python at all — so there is no JobRun row to inspect, failed or
    otherwise. A check looking for SKIPPED rows would see a clean history and
    report everything fine while coverage had not run for a day.
    """
    now = now or timezone.now()
    runs = JobRun.objects.filter(name="coverage")
    latest = runs.order_by("-started_at").values_list("started_at", flat=True).first()
    if latest is None:
        return Check("coverage_progress", False,
                     "The coverage job has never run. Removal detection cannot "
                     "prove an ad is gone until the feed is covered end to end.",
                     {"last_run_at": None})

    age = now - latest
    recent = runs.filter(started_at__gte=now - timedelta(hours=RECENT_HOURS))
    skipped = recent.filter(status=JobRun.Status.SKIPPED).count()
    data = {
        "last_run_at": latest.isoformat(),
        "age_hours": round(age.total_seconds() / 3600, 1),
        "runs_24h": recent.count(),
        "skipped_24h": skipped,
    }
    if age > COVERAGE_STARVED_AFTER:
        return Check(
            "coverage_progress", False,
            f"Coverage last ran {data['age_hours']:.1f}h ago. It is starved — most "
            f"likely losing the shared fetch lock to a long hot tick. Removal "
            f"detection stays paused while the deep tail goes unread.",
            data,
        )
    return Check("coverage_progress", True,
                 f"Coverage ran {data['age_hours']:.1f}h ago; {data['runs_24h']} run(s) "
                 f"in {RECENT_HOURS:.0f}h ({skipped} skipped).", data)


def check_ingest_progress(now=None) -> Check:
    """A crawler that runs but stores nothing is worse than one that crashes.

    A silent ban or a payload-shape change shows up here first: runs keep
    succeeding, pages keep being fetched, and created+updated goes to zero.
    """
    now = now or timezone.now()
    agg = FetchRun.objects.filter(
        started_at__gte=now - timedelta(hours=RECENT_HOURS), status=FetchRun.Status.SUCCEEDED
    ).aggregate(runs=Count("id"), fetched=Sum("fetched_count"), pages=Sum("pages_fetched"))
    runs, fetched, pages = agg["runs"] or 0, agg["fetched"] or 0, agg["pages"] or 0
    if runs == 0:
        return Check("ingest_progress", False,
                     f"No successful run at all in {RECENT_HOURS:.0f}h — the worker "
                     f"is not running.", {"runs": 0})
    if pages > 0 and fetched == 0:
        return Check("ingest_progress", False,
                     f"{runs} run(s) fetched {pages} page(s) but ingested 0 ads — "
                     f"likely a block or a payload-shape change.",
                     {"runs": runs, "pages": pages, "fetched": 0})
    return Check("ingest_progress", True,
                 f"{runs} successful run(s), {pages} page(s), {fetched} ad(s) ingested "
                 f"in {RECENT_HOURS:.0f}h.",
                 {"runs": runs, "pages": pages, "fetched": fetched})


def check_upstream_outage(now=None) -> Check:
    """Is bama.ir failing us, and are we backing off rather than hammering it?

    Separate from ``failed_runs`` because they answer different questions.
    ``failed_runs`` is a 24h count and stays red for a day after a two-hour
    outage that already healed — useful history, useless for "is it broken right
    now". This one is instantaneous, and it is the one that gets alerted on.
    """
    from apps.jobs.fetcher import consecutive_failures, upstream_cooldown_until

    now = now or timezone.now()
    streak = consecutive_failures()
    until = upstream_cooldown_until()
    # The breaker, not the raw streak: one failed fetch is ordinary and is
    # already on `failed_runs`. Going red here on a single blip pages someone
    # for a host that answered on the next tick, and the copy used to say
    # "backing off" even when `until` was None.
    if until is None:
        return Check("upstream_outage", True, "bama.ir is answering.")
    remaining = max(0.0, (until - now).total_seconds())
    if remaining > 0:
        detail = (
            f"bama.ir failed {streak} consecutive fetch(es). Backing off; next "
            f"attempt in {remaining / 60:.1f} min. Stored data and the deal "
            f"board are unaffected — only new listings are delayed."
        )
    else:
        detail = (
            f"bama.ir failed {streak} consecutive fetch(es). The cooldown has "
            f"lapsed; the next tick will retry. Stored data and the deal board "
            f"are unaffected."
        )
    return Check(
        "upstream_outage", False, detail,
        {"consecutive_failures": streak, "next_attempt_at": until.isoformat()},
    )


def check_removal_detection(now=None) -> Check:
    """Can the system still prove an ad has left the feed?

    The check that did not exist while the thing it watches was broken. On
    2026-09-06 removal detection had been unable to conclude anything for 14
    hours, 918 ads were sitting in UNVERIFIED with nothing able to adjudicate
    them, and every other check on this page was green — because each one was
    watching an input to removal detection rather than removal detection itself.

    `sweep_freshness` and `coverage_progress` remain the *causes* to read once
    this is red. This is the effect, and the effect is what has a user-visible
    consequence: a car the app still shows as for sale.
    """
    now = now or timezone.now()
    cutoff, windows = sweep_cutoff()
    unverified = Ad.objects.filter(status=Ad.Status.UNVERIFIED).count()
    oldest = (Ad.objects.filter(status=Ad.Status.UNVERIFIED)
              .order_by("last_seen_at").values_list("last_seen_at", flat=True).first())
    stuck_hours = (now - oldest).total_seconds() / 3600 if oldest else 0.0
    data = {"windows_complete": windows, "required": REQUIRED_MISSED_WINDOWS,
            "unverified": unverified, "stuck_hours": round(stuck_hours, 1)}
    if cutoff is not None:
        return Check("removal_detection", True,
                     f"Both {COVERAGE_WINDOW_HOURS:.0f}h windows are covered; absence "
                     f"is provable and {unverified} ad(s) await adjudication.", data)
    return Check(
        "removal_detection", False,
        f"Cannot prove any ad is gone: only {windows} of {REQUIRED_MISSED_WINDOWS} "
        f"consecutive {COVERAGE_WINDOW_HOURS:.0f}h windows are fully covered. "
        f"{unverified} ad(s) are stranded in UNVERIFIED"
        + (f", the oldest unseen for {stuck_hours:.0f}h" if oldest else "")
        + ". Sold cars keep showing as for sale until coverage closes.",
        data,
    )


# A model refused every night for this long is not a run of bad luck; either the
# challenger really is worse and somebody should look, or the gate is asking a
# question the trainer cannot answer. Both need a human, and neither announces
# itself — four models sat frozen for five days with the trainer reporting
# success every night, because "trained" and "promoted" are different words and
# only the first one was on the page.
MODEL_STALE_AFTER = timedelta(days=4)

# Nightly training refused the challenger for one of these reasons — the
# incumbent is correctly held, not stuck. Reporting that as ops failure made
# `model_staleness` red for two models that were legitimately beating every
# challenger while the promotion gate did exactly what it was written to do.
_HOLD_REASONS = frozenset({
    "loses_to_incumbent", "loses_to_baseline", "loses_to_both",
})


def check_model_staleness(now=None) -> Check:
    """Is anything still serving predictions from a model nobody can replace?

    Old is not stuck when a newer challenger lost on the holdout. It is stuck
    when the trainer has gone silent (no newer row) or when the newest row was
    refused for a reason that is not a real holdout loss.
    """
    from apps.ml.models import MLModel

    now = now or timezone.now()
    stuck = []
    held = []
    for record in MLModel.objects.filter(status=MLModel.Status.ACTIVE):
        if not record.trained_at or now - record.trained_at <= MODEL_STALE_AFTER:
            continue
        newest = (MLModel.objects.filter(name=record.name)
                  .order_by("-version").values("version", "metrics").first()) or {}
        latest_version = newest.get("version")
        if latest_version is None or latest_version == record.version:
            # No newer row: the trainer is silent, not holding a winner.
            stuck.append({
                "name": record.name, "serving": record.version,
                "age_days": round((now - record.trained_at).total_seconds() / 86400, 1),
                "latest": record.version, "refused_because": "no_challenger_trained",
            })
            continue
        reason = ((newest.get("metrics") or {}).get("promotion") or {}).get("reason", "?")
        row = {"name": record.name, "serving": record.version,
               "age_days": round((now - record.trained_at).total_seconds() / 86400, 1),
               "latest": latest_version, "refused_because": reason}
        if reason in _HOLD_REASONS:
            held.append(row)
            continue
        stuck.append(row)
    if not stuck:
        detail = "Every active model is current."
        if held:
            detail += (
                f" {len(held)} older incumbent(s) correctly held after nightly "
                f"training refused a worse challenger."
            )
        return Check("model_staleness", True, detail, {"held": held})
    detail = "; ".join(
        f"{m['name']} serving v{m['serving']} ({m['age_days']:.0f}d old), "
        f"v{m['latest']} refused: {m['refused_because']}" for m in stuck
    )
    return Check("model_staleness", False,
                 f"{len(stuck)} model(s) stuck on a stale version. {detail}",
                 {"stuck": stuck, "held": held})


# A nightly job that stops running produces silence, and silence is what success
# also looks like. 26 hours, not 24: the dump runs at 23:00 UTC and takes about a
# minute, so a 24h bar would go red on clock jitter alone every night.
BACKUP_STALE_AFTER = timedelta(hours=26)


def check_backup_freshness(now=None) -> Check:
    """Did last night's database dump actually happen?

    The backup script alerts loudly when it *fails*. What neither it nor anything
    else could detect is the cron never firing at all — a disabled crontab, a
    renamed script, a host that rebooted into a broken state. That failure mode
    is invisible by construction: it produces no error, no log line, and no file,
    and the newest backup simply stops getting newer while everything reads fine.

    Watches the artifact rather than the job, because the artifact is the thing
    with the value. A run that "succeeded" and left no file is the same incident
    as a run that never happened, and this notices both.
    """
    now = now or timezone.now()
    directory = getattr(settings, "BAMA_BACKUP_DIR", "") or ""
    if not directory:
        return Check("backup_freshness", True,
                     "No backup directory configured for this environment.")
    path = Path(directory)
    if not path.is_dir():
        return Check("backup_freshness", False,
                     f"{directory} is not a directory. The backup volume is not "
                     f"mounted, so nothing here can confirm a dump exists.",
                     {"backup_dir": directory})

    dumps = sorted(path.glob("daily-*.dump.enc"), key=lambda p: p.stat().st_mtime)
    if not dumps:
        return Check("backup_freshness", False,
                     f"No daily-*.dump.enc in {directory}. There is no restorable "
                     f"copy of this database.", {"backup_dir": directory, "count": 0})

    newest = dumps[-1]
    stat = newest.stat()
    age = now - datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    hours = age.total_seconds() / 3600
    # Reported alongside the age because the two failures look identical from a
    # timestamp alone: a dump that never ran, and a dump that ran and wrote
    # almost nothing because the database was unreachable.
    data = {"backup_dir": directory, "newest": newest.name, "count": len(dumps),
            "age_hours": round(hours, 1), "size_mb": round(stat.st_size / 1e6, 1)}
    # `.rejected` files are the backup script's own verification failing; it has
    # already alerted about those, and counting them here would report the same
    # incident twice under a name that sends you to the wrong place.
    if age > BACKUP_STALE_AFTER:
        return Check("backup_freshness", False,
                     f"Newest backup {newest.name} is {hours:.0f}h old "
                     f"({BACKUP_STALE_AFTER.total_seconds() / 3600:.0f}h is the bar). "
                     f"The nightly dump has stopped running; every hour from here "
                     f"widens what a restore would lose.", data)
    return Check("backup_freshness", True,
                 f"{len(dumps)} dump(s) retained, newest {newest.name} "
                 f"{hours:.0f}h old ({data['size_mb']:.0f}MB).", data)


def check_telegram_configured(now=None) -> Check:
    """Do enabled senders have the required token and chat ID?

    The chat id and ``enabled`` flag live in the database and can look fully
    configured while the token is an empty string. Compose spells it
    ``${BAMA_TELEGRAM_TOKEN:-}``, so a missing ``.env.production`` key resolves
    to ``""`` without error, and ``docker exec env`` still lists the name.
    That combination silenced four channels on 2026-09-07 — the deal feed, the
    per-user alerts, the health alerts, and the backup script's own failure
    alarm — with zero errors anywhere.

    The operator switch is not the only sender. Per-user alert rules and the
    nightly backup script read the same token and ignore that switch. Empty
    token is a laptop only when nothing here is trying to send.
    """
    from apps.accounts.models import AlertRule

    cfg = NotifierSettings.load()
    token = (getattr(settings, "BAMA_TELEGRAM_TOKEN", "") or "").strip()
    chat = (cfg.telegram_chat_id or "").strip()
    has_token = bool(token)
    has_chat = bool(chat)
    user_chats = AlertRule.objects.filter(enabled=True, telegram_chat_id__gt="").exists()
    backups_configured = bool(getattr(settings, "BAMA_BACKUP_DIR", "") or "")
    needs_token = cfg.enabled or user_chats or backups_configured
    # Length only. The value is a secret and must not land in JobRun.detail,
    # the Control page, or a health-alert message.
    data = {"enabled": cfg.enabled, "has_chat": has_chat, "has_token": has_token,
            "token_len": len(token), "user_chats": user_chats,
            "backups_configured": backups_configured}
    if not has_token and not needs_token:
        return Check("telegram_configured", True,
                     "No live sender needs a token; an empty one is expected.", data)
    if has_token and (not cfg.enabled or has_chat):
        return Check("telegram_configured", True,
                     "Sender credentials are configured; delivery is not verified.", data)
    missing = []
    if not has_token:
        missing.append("token")
    if cfg.enabled and not has_chat:
        missing.append("chat id")
    joined = " and ".join(missing)
    verb = "is" if len(missing) == 1 else "are"
    who = []
    if cfg.enabled:
        who.append("operator")
    if user_chats:
        who.append("user alerts")
    if backups_configured:
        who.append("backups")
    return Check(
        "telegram_configured", False,
        f"{' / '.join(who) or 'Notifier'} "
        f"{'needs' if len(who) == 1 else 'need'} a working channel but {joined} "
        f"{verb} empty. A missing BAMA_TELEGRAM_TOKEN key becomes \"\" in "
        f"compose without error; check the value's length, not whether the "
        f"name exists.",
        data,
    )


# Source block first: when it is active it is the cause of everything below, and
# reading the consequences before the cause wastes the operator's time.
# `upstream_outage` sits beside it for the same reason: both say "the problem is
# not us", and reading that first stops an operator debugging their own crawler.
#
# `removal_detection` is the effect that `sweep_freshness` and
# `coverage_progress` are the causes of, so it reads immediately after them.
CHECKS = (check_source_block, check_upstream_outage,
          check_sweep_freshness, check_coverage_progress, check_removal_detection,
          check_failed_runs, check_reject_spike, check_ingest_progress,
          check_model_staleness, check_backup_freshness,
          check_telegram_configured)


def run_checks(now=None) -> list[Check]:
    """Run every check. A broken check reports itself rather than exploding."""
    results = []
    for check in CHECKS:
        try:
            results.append(check(now))
        except Exception as exc:  # noqa: BLE001 — a monitor must not crash
            results.append(Check(check.__name__.removeprefix("check_"), False,
                                 f"check raised {exc!r}"))
    return results


def _previous_red() -> set[str] | None:
    """Which checks were red last time this job ran, or None if it never has.

    Read back out of the previous ``JobRun``'s detail rather than kept in the
    cache, because Redis here is capped and LRU-evicting: a monitor whose memory
    can be evicted under load is a monitor that re-announces an ongoing incident
    at the worst possible moment. The detail string is written by this same
    function one run earlier, so the format has exactly one author.

    None, not an empty set, when there is no previous run — a first run must not
    read as "everything just broke" and alert on a steady state it never saw.
    """
    previous = (JobRun.objects.filter(name="health")
                .exclude(status=JobRun.Status.RUNNING)
                .order_by("-started_at").values_list("detail", flat=True).first())
    if previous is None:
        return None
    for token in (previous or "").split():
        if token.startswith("red="):
            names = token[len("red="):]
            return set(names.split(",")) if names and names != "-" else set()
    # Pre-upgrade rows are `ok=True` / `ok=False` with no `red=` token.
    # An empty set here would mean "everything was green", so every currently
    # red check would page as breaking news on the first warm tick after deploy.
    return None


def health(*, alert: bool = True, dry_run: bool = False) -> dict:
    """Crawl health as a job. ``ok`` is False if any check failed.

    Also the thing that *tells somebody*. This used to be a pure report whose
    only reader was a log line, which is why three separate multi-hour failures
    on 2026-09-05/06 went unannounced. Alerting on the transition rather than on
    the state keeps it quiet enough to stay switched on.
    """
    checks = run_checks()
    red = {c.name for c in checks if not c.ok}
    was_red = _previous_red()

    delivery: dict = {"changed": 0, "sent": 0}
    newly_red = [asdict(c) for c in checks if not c.ok and c.name not in (was_red or ())]
    recovered = sorted((was_red or set()) - red)
    # `was_red is None` is a first run: there is no transition, only a state,
    # and announcing a standing backlog as breaking news is how a fresh deploy
    # would page somebody about a problem that predates it.
    if alert and was_red is not None and (newly_red or recovered):
        try:
            delivery = deliver_health_alert(newly_red=newly_red, recovered=recovered,
                                            dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 — a monitor must not crash
            logger.warning("event=health_alert_failed error=%r", exc)
            delivery = {"changed": len(newly_red) + len(recovered), "sent": 0,
                        "error": repr(exc)[:200]}

    logger.info(
        "event=health_report ok=%s red=%s new=%s recovered=%s alerts_sent=%s",
        not red, ",".join(sorted(red)) or "-",
        ",".join(sorted(c["name"] for c in newly_red)) or "-",
        ",".join(recovered) or "-", delivery.get("sent", 0),
    )
    # One line per failing check, so "why is it red" is answerable from the
    # worker log alone. The checks already carry their own explanation; nothing
    # was reading it because the only thing printed was the aggregate boolean.
    for check in checks:
        if not check.ok:
            logger.warning("event=health_check_red name=%s detail=%s",
                           check.name, check.detail)
    return {
        "ok": not red,
        # `red=` is the durable half: `_previous_red` parses it back next run.
        # "-" rather than "" so the token survives whitespace-splitting.
        "red": ",".join(sorted(red)) or "-",
        "alerts_sent": delivery.get("sent", 0),
        "checks": [asdict(c) for c in checks],
    }
