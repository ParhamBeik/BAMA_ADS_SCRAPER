#!/usr/bin/env sh
# Slow-cadence upkeep: full deal-score rebuild, provenance pruning, health report.
#
# Split out of the old sweep script so that fetching and upkeep no longer share
# a fate. Previously a network timeout on page 619 aborted the whole chain, so
# the deal board went un-rebuilt and nothing was pruned for as long as the feed
# misbehaved — the crawl's worst days were also the days maintenance stopped.
#
# The hot tick refreshes deal scores incrementally for the cohorts it touched;
# this full rebuild is what catches cohorts nothing touched (an ad going stale,
# a peer being delisted).
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # → bama-saas/
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

mkdir -p "$PROJECT_DIR/logs"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.dev}"

LOCK="${BAMA_MAINTENANCE_LOCK:-/tmp/bama-saas-maintenance.lock}"

# crawl_health stays last and its exit status stays deliberately swallowed: it
# is a report, not a step, and a red crawler must not make maintenance look as
# though it failed to run. The findings go to the log.
RUN='set -eu
     PY="$1"; shift
     "$PY" manage.py compute_deal_scores
     "$PY" manage.py prune_history --days 30 || echo "bama.maint: prune_history failed (see above)" >&2
     echo "--- crawl health ---"
     "$PY" manage.py crawl_health || echo "bama.maint: CRAWL HEALTH DEGRADED (see above)" >&2'

if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" sh -c "$RUN" _ "$PYTHON_BIN" "$@"
else
    echo "bama.maint: flock not found on this host; running unlocked (non-Linux dev host)." >&2
    exec sh -c "$RUN" _ "$PYTHON_BIN" "$@"
fi
