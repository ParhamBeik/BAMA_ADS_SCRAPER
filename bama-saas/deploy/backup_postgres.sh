#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Prod by default: this script only ever runs on the VPS, and pointing it at the
# dev compose file makes `exec postgres` miss the running container entirely —
# a backup job that silently backs up nothing is worse than no backup job.
compose_file="${COMPOSE_FILE:-${project_dir}/docker-compose.prod.yml}"
env_file="${ENV_FILE:-${project_dir}/.env.production}"
backup_dir="${BACKUP_DIR:-/var/backups/bama}"
# The schema has 33 tables today. The floor is a sanity bar against a truncated
# or wrong-database archive, not a schema assertion — leave room to add apps.
MIN_EXPECTED_TABLES="${MIN_EXPECTED_TABLES:-20}"
passphrase_file="${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE to a mode-400 or mode-600 file outside the repository}"
compose=(docker compose --project-directory "${project_dir}" -f "${compose_file}")
if [[ -r "${env_file}" ]]; then
  compose+=(--env-file "${env_file}")
fi

# Everything this script says goes to a log nobody watches until the day it
# matters, so stamp every line. "Created ..." with no date cannot tell you
# whether the job stopped running three weeks ago.
say() { echo "$(date -Is) $*"; }
die() { echo "$(date -Is) FAILED: $*" >&2; exit 1; }

[[ -r "${passphrase_file}" ]] || die "cannot read ${passphrase_file}"
mode="$(stat -c '%a' "${passphrase_file}")"
[[ "${mode}" =~ ^[46]00$ ]] || die "${passphrase_file} must have mode 400 or 600"
mkdir -p "${backup_dir}"

stamp="$(TZ=Asia/Tehran date +%F)"
partial="${backup_dir}/daily-${stamp}.dump.enc.partial"
destination="${partial%.partial}"
trap 'rm -f "${partial}"' EXIT

"${compose[@]}" exec -T postgres sh -c \
  'exec pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 310000 \
      -pass "file:${passphrase_file}" -out "${partial}"
mv "${partial}" "${destination}"

# Verify by reading the archive back through the passphrase we just used. Two
# checks, because neither alone is enough — measured, not assumed:
#
#   1. Full decrypt to /dev/null. A truncated file fails the final CBC padding
#      block ("bad decrypt"), which is what a disk filling up mid-dump looks
#      like. Costs under a second for 300MB.
#   2. TOC listing. Proves the plaintext is a real archive of *this* database
#      and not, say, a valid dump of the wrong container.
#
# ponytail: what this still does NOT catch is a single flipped byte in the
# middle — CBC is malleable, so corruption there decrypts clean and only shows
# up when you actually restore (tested: exit 0). The .sha256 alongside guards
# bit-rot after the fact. If that gap ever matters, the upgrade is a nightly
# restore into a scratch database; it costs minutes rather than seconds.

if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
     -pass "file:${passphrase_file}" -in "${destination}" >/dev/null 2>&1; then
  die "${destination} does not decrypt cleanly end to end (truncated or corrupt). Left in place for inspection."
fi

# Judged on output, not exit status, deliberately: `pg_restore --list` reads the
# header and TOC and then exits, which slams the pipe shut on the openssl still
# streaming 300MB behind it. Under `pipefail` that surfaces as a failed backup
# when in fact the archive is fine, so `set +o pipefail` is scoped to this one
# subshell and the real check is on what came out the far end.
toc="$(set +o pipefail
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
    -pass "file:${passphrase_file}" -in "${destination}" 2>/dev/null \
  | "${compose[@]}" exec -T postgres pg_restore --list 2>/dev/null)"

tables="$(grep -c 'TABLE DATA' <<<"${toc}" || true)"
if ((tables < MIN_EXPECTED_TABLES)) || ! grep -q 'TABLE DATA public accounts_user' <<<"${toc}"; then
  die "decrypted archive lists ${tables} tables and no accounts_user; expected at least ${MIN_EXPECTED_TABLES}. Keeping ${destination} unchecksummed for inspection."
fi

sha256sum "${destination}" > "${destination}.sha256"

mapfile -t old_backups < <(find "${backup_dir}" -maxdepth 1 -type f -name 'daily-*.dump.enc' -print | sort -r)
if ((${#old_backups[@]} > 7)); then
  for backup in "${old_backups[@]:7}"; do rm -f -- "${backup}" "${backup}.sha256"; done
fi
say "created ${destination} ($(du -h "${destination}" | cut -f1), ${tables} tables)"
