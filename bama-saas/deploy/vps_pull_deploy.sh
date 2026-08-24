#!/usr/bin/env bash
# Rebuild and restart the prod stack from the code already checked out here.
#
# Not the thing that updates the checkout — that's the tiny wrapper on the
# VPS (outside this repo, so `git reset --hard` mid-deploy can never rewrite
# a script bash is still reading): it fetches, resets to origin/main, *then*
# calls this script, which only ever runs as a complete, already-on-disk
# file. Keep it that way; don't merge the fetch/reset into this file.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() {
    docker compose -f docker-compose.prod.yml --env-file .env.production "$@"
}

# Build first, so a compile/asset error fails the deploy while the previous
# containers are still serving traffic.
compose build

# Migrate as a one-off *before* replacing anything. The django service also
# migrates on start, but by then the old container is already gone — a bad
# migration leaves it crash-looping with nothing serving. Running it here means
# `set -e` aborts the deploy and the running stack is left untouched.
# --wait blocks until the healthcheck passes, so migrate cannot race the
# database still doing crash recovery.
compose up -d --wait postgres
compose run --rm --no-deps django python manage.py migrate --noinput

compose up -d
docker image prune -f
