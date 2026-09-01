#!/usr/bin/env sh
# The training loop — PID 1 of the compose `ml` service.
#
#   train.sh          run the loop forever
#   train.sh once     fit every model once and exit
#
# Its own container and its own loop, rather than another cadence inside
# worker.sh, for one reason: every step in there is seconds of local arithmetic
# and a full refit is minutes of saturated CPU. On a VPS running three stacks,
# a LightGBM fit inside the worker loop would compete with the fetch tick for
# the same cores and make the crawl look slow for reasons nothing in the crawl
# logs would explain.
#
# Daily by default, and that is not a throughput decision. The promotion gate
# compares a challenger against the incumbent on a fresh holdout, and a holdout
# an hour wide is noise: retraining every hour would swap models on sampling
# error and change the number on a card several times a day.
#
# Deliberately no `set -e` in the loop, for the same reason as worker.sh: a
# failed fit must be logged and retried on the next tick, never take the
# container down. A crash-looping trainer trains nothing and hides the original
# error behind restart noise.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

if [ -z "${PYTHON_BIN:-}" ]; then
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

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] train: $*"; }

tick() {
    # The same flock discipline as the worker. Two concurrent fits would both
    # write artifacts and both call `promote`, and the second would retire a
    # model the first had just made live.
    lock="/tmp/bama-train.lock"
    if command -v flock >/dev/null 2>&1; then
        flock -n "$lock" "$PYTHON_BIN" manage.py bama train
    else
        "$PYTHON_BIN" manage.py bama train
    fi
}

if [ "${1:-}" = "once" ]; then
    tick
    exit $?
fi

TRAIN_EVERY="${BAMA_TRAIN_EVERY:-86400}"
# Wait for the first hot tick to have filled the board before the first fit.
# On a cold database there is nothing to learn from, and the trainers would all
# refuse with `insufficient_rows` — a correct answer, but a noisy way to start.
TRAIN_DELAY="${BAMA_TRAIN_DELAY:-600}"

log "started (every=${TRAIN_EVERY}s first run in ${TRAIN_DELAY}s)"
sleep "$TRAIN_DELAY"

while true; do
    log "cycle start"
    if tick; then
        log "cycle ok"
    else
        log "cycle FAILED rc=$? (retrying next cycle)" >&2
    fi
    sleep "$TRAIN_EVERY"
done
