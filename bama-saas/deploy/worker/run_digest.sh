#!/usr/bin/env sh
# Digest tick — email/Telegram + in-app digest per engaged user.
#
# Self-locating (safe from cron with no CWD), env-aware (sources .env). Default
# kind is ``daily``; pass ``weekly`` for the weekly digest:
#
#     ./run_digest.sh            # daily
#     ./run_digest.sh weekly     # weekly
#
# Install with ./install_cron.sh (manages the digest entry alongside the others).
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

KIND="${1:-daily}"
exec "$PYTHON_BIN" manage.py send_digest --kind "$KIND"
