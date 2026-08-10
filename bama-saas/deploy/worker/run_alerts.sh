#!/usr/bin/env sh
# Alert-evaluation tick — evaluate every enabled user Alert and dispatch
# notifications (price drops, undervalued, new listings).
#
# Self-locating (safe from cron with no CWD), env-aware (sources .env so host
# cron picks up DATABASE_URL / BAMA_* / TELEGRAM_BOT_TOKEN). Runs on a slower
# cadence than the data pipeline (default every 30 min) because delivery
# (email/Telegram) is slower and must not stall the 5-min data tick.
#
# Install with ./install_cron.sh (manages the alerts entry alongside the others).
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

LOCK="${BAMA_ALERTS_LOCK:-/tmp/bama-saas-alerts.lock}"
if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" "$PYTHON_BIN" manage.py evaluate_alerts "$@"
else
    echo "bama.alerts: flock not found on this host; running unlocked." >&2
    exec "$PYTHON_BIN" manage.py evaluate_alerts "$@"
fi
