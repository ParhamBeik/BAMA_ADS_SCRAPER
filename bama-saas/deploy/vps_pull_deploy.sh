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

docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker image prune -f
