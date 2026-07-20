#!/usr/bin/env sh
# Worker tick — one scheduled data pipeline run.
#
# Self-locating (safe to call from cron with no CWD), self-locking (flock so two
# overlapping 5-minute ticks never race), and env-aware (sources .env so host
# cron picks up DATABASE_URL / BAMA_*). All args are forwarded to
# `python manage.py run_pipeline`, e.g. `run_pipeline.sh --skip-fetch`.
#
# Install the cron entry with ./install_cron.sh (see that script).
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # → bama-saas/
cd "$PROJECT_DIR"

# Pick a Python: explicit override > local venv > system python.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

# Load env vars (DATABASE_URL, BAMA_*) if a local .env exists.
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

mkdir -p "$PROJECT_DIR/logs"

# DJANGO_SETTINGS_MODULE defaults to dev on the host; prod is set via env.
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.dev}"

# Exclusive lock: a still-running tick makes the next one exit 0 (no-op).
# flock is a Linux util (util-linux); on hosts without it (e.g. macOS dev) we
# fall back to running unlocked — production targets Linux cron / Docker, where
# flock is present and the concurrency guard is enforced.
LOCK="${BAMA_WORKER_LOCK:-/tmp/bama-saas-worker.lock}"
if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" "$PYTHON_BIN" manage.py run_pipeline "$@"
else
    echo "bama.worker: flock not found on this host; running unlocked (non-Linux dev host)." >&2
    exec "$PYTHON_BIN" manage.py run_pipeline "$@"
fi
