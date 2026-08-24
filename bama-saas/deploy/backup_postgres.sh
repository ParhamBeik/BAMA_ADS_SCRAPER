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

# Read one KEY=value from the env file without sourcing the rest of it
# (passwords, the dump passphrase, and the bot token all live there).
_env_get() {
  local key="$1"
  [[ -r "${env_file}" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "${env_file}" | tail -n 1)" || true
  [[ -n "${line}" ]] || return 0
  line="${line#"${key}="}"
  line="${line#\"}"; line="${line%\"}"
  line="${line#\'}"; line="${line%\'}"
  printf '%s' "${line}"
}

# Everything this script says goes to a log nobody watches until the day it
# matters, so stamp every line. "Created ..." with no date cannot tell you
# whether the job stopped running three weeks ago.
say() { echo "$(date -Is) $*"; }

# A failed backup that nobody hears about is the same as no backup. Telegram
# is best-effort: a down bot must not change the exit code, and missing
# credentials must not look like success.
alert_failure() {
  local msg="$1"
  local token chat
  token="${BAMA_TELEGRAM_TOKEN:-$(_env_get BAMA_TELEGRAM_TOKEN)}"
  chat="${BACKUP_TELEGRAM_CHAT_ID:-$(_env_get BACKUP_TELEGRAM_CHAT_ID)}"
  if [[ -z "${chat}" ]]; then
    chat="$("${compose[@]}" exec -T postgres \
      sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT telegram_chat_id FROM analytics_notifiersettings WHERE id=1"' \
      2>/dev/null | tr -d "[:space:]")" || true
  fi
  if [[ -z "${token}" || -z "${chat}" ]]; then
    say "alert skipped: telegram token or chat id missing"
    return 0
  fi
  curl -sS --max-time 10 -X POST \
    "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=${msg}" \
    >/dev/null 2>&1 || say "alert send failed (backup still failed)"
}

die() {
  say "FAILED: $*" >&2
  alert_failure "Bama nightly backup FAILED: $*" || true
  exit 1
}

[[ -r "${passphrase_file}" ]] || die "cannot read ${passphrase_file}"
mode="$(stat -c '%a' "${passphrase_file}")"
[[ "${mode}" =~ ^[46]00$ ]] || die "${passphrase_file} must have mode 400 or 600"
mkdir -p "${backup_dir}"

stamp="$(TZ=Asia/Tehran date +%F)"
partial="${backup_dir}/daily-${stamp}.dump.enc.partial"
destination="${partial%.partial}"
trap 'rm -f "${partial}"' EXIT

# `|| die` rather than leaving it to `set -e`: the commonest failure here is the
# postgres container being down, and docker's own "service is not running" goes
# to stderr untimestamped. Without this the log's only dated lines are the
# successes — exactly backwards for something you read after a bad night.
"${compose[@]}" exec -T postgres sh -c \
  'exec pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 310000 \
      -pass "file:${passphrase_file}" -out "${partial}" \
  || die "dump/encrypt pipeline failed (is the postgres container up?)"
mv "${partial}" "${destination}"

# A file that fails verification below gets renamed out of the way rather than
# left sitting there: retention keeps the newest 7 matching daily-*.dump.enc
# without caring whether they are any good, so a run of bad nights would quietly
# evict the last known-good backup. `.rejected` does not match the glob.
reject() { mv -f "${destination}" "${destination}.rejected" 2>/dev/null || true; die "$*"; }

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
  reject "${destination} does not decrypt cleanly end to end (truncated or corrupt); renamed to .rejected."
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
# Reported separately rather than as one merged sentence: these fail for very
# different reasons (a truncated archive vs. a dump of the wrong database), and
# a message that names the wrong one sends you looking in the wrong place.
if grep -q 'TABLE DATA public accounts_user' <<<"${toc}"; then has_user=yes; else has_user=no; fi
if ((tables < MIN_EXPECTED_TABLES)) || [[ "${has_user}" == no ]]; then
  reject "archive lists ${tables} tables (need >=${MIN_EXPECTED_TABLES}), accounts_user present=${has_user}; renamed to .rejected."
fi

sha256sum "${destination}" > "${destination}.sha256"

mapfile -t old_backups < <(find "${backup_dir}" -maxdepth 1 -type f -name 'daily-*.dump.enc' -print | sort -r)
if ((${#old_backups[@]} > 7)); then
  for backup in "${old_backups[@]:7}"; do rm -f -- "${backup}" "${backup}.sha256"; done
fi

# Rejected archives are diagnostics, not backups. Keep the two newest so a
# recurring failure cannot fill the disk at 300MB a night. (An `if` rather than
# `(( )) &&` — a false condition returns 1, which under `set -e` would abort the
# script on the last line and report a successful backup as a failure.)
mapfile -t rejected < <(find "${backup_dir}" -maxdepth 1 -type f -name 'daily-*.dump.enc.rejected' -print | sort -r)
if ((${#rejected[@]} > 2)); then
  rm -f -- "${rejected[@]:2}"
fi
say "created ${destination} ($(du -h "${destination}" | cut -f1), ${tables} tables)"
