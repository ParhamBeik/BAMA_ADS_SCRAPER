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

# --wait blocks until the healthcheck passes, so migrate cannot race the
# database still doing crash recovery.
compose up -d --wait postgres

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
compose stop django worker

# Migrate as a one-off rather than letting the django service do it on start:
# `set -e` then aborts the deploy on a bad migration, and `compose up -d` below
# is never reached, so the stack is restarted on the old image instead of
# crash-looping on the new one.
compose run --rm --no-deps django python manage.py migrate --noinput

compose up -d
docker image prune -f
