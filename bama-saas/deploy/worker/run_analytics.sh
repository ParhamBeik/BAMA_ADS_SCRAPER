#!/usr/bin/env sh
# Warm analytics tick — episodes + snapshots + market index.
# Does not fetch. Shares the pipeline flock so it never overlaps a HOT tick.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
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

LOCK="${BAMA_WORKER_LOCK:-/tmp/bama-saas-worker.lock}"
if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" "$PYTHON_BIN" manage.py run_pipeline --cadence warm --skip-fetch "$@"
else
    echo "bama.worker: flock not found on this host; running unlocked (non-Linux dev host)." >&2
    exec "$PYTHON_BIN" manage.py run_pipeline --cadence warm --skip-fetch "$@"
fi
