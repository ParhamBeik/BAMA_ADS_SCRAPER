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

# Build only application images. Postgres and Redis are pulled images; rebuilding
# them adds time and can never include repository changes.
compose build django worker ml frontend

# --wait blocks until the healthchecks pass, so migrate cannot race the
# database or cache still doing startup recovery.
compose up -d --wait postgres redis

# Stop the app containers before touching the schema, and accept a few seconds
# of downtime for it.
#
# The obvious ordering — migrate first, then swap containers — leaves the OLD
# code running against the NEW schema for the length of the swap. That is not
# theoretical: the deploy that dropped accounts_user.is_demo did exactly this,
# and Django names every model field explicitly in its SELECTs, including the
# one the session middleware runs to load request.user. Every authenticated
# request in that window would have hit an UndefinedColumn error and returned
# 500. A brief, honest outage beats a burst of errors nobody is watching for.
#
# The alternative is expand/contract migrations, which is the right answer for a
# service that cannot go down. This one can.
#
# The frontend is a static nginx bundle and keeps serving the site throughout;
# its API calls fail while django is stopped, and `up` below recreates it on
# the new image.
compose stop django worker ml

# Migrate as a one-off rather than letting the django service do it on start,
# so a bad migration aborts the deploy before `up` recreates anything. On
# failure, start the stopped containers again: `start` reuses the existing
# containers, which still hold the previous image, rather than the rebuilt one.
# Django runs each atomic migration in its own transaction on PostgreSQL, so
# the failed one left nothing behind; earlier ones in the batch did apply.
if ! compose run --rm --no-deps django python manage.py migrate --noinput; then
    echo "migrate failed; restarting the previous containers" >&2
    compose start django worker ml || true
    exit 1
fi

compose up -d --wait --no-build
# Retain prior images for rollback; reclaim space only after verifying the release.
