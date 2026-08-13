#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
env_file="${ENV_FILE:-${project_dir}/.env.production}"
backup_dir="${BACKUP_DIR:-/var/backups/bama}"
passphrase_file="${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE to a mode-400 or mode-600 file outside the repository}"
compose=(docker compose -f "${project_dir}/deploy/docker-compose.prod.yml" --project-directory "${project_dir}" --env-file "${env_file}")

[[ -r "${env_file}" ]] || { echo "Cannot read ${env_file}" >&2; exit 1; }
[[ -r "${passphrase_file}" ]] || { echo "Cannot read ${passphrase_file}" >&2; exit 1; }
mode="$(stat -c '%a' "${passphrase_file}")"
[[ "${mode}" =~ ^[46]00$ ]] || { echo "${passphrase_file} must have mode 400 or 600" >&2; exit 1; }
mkdir -p "${backup_dir}"

stamp="$(TZ=Asia/Tehran date +%F)"
partial="${backup_dir}/daily-${stamp}.dump.enc.partial"
destination="${partial%.partial}"
trap 'rm -f "${partial}"' EXIT

"${compose[@]}" exec -T db sh -c \
  'exec pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 310000 \
      -pass "file:${passphrase_file}" -out "${partial}"
mv "${partial}" "${destination}"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
  -pass "file:${passphrase_file}" -in "${destination}" \
  | "${compose[@]}" exec -T db pg_restore --list >/dev/null
sha256sum "${destination}" > "${destination}.sha256"

mapfile -t old_backups < <(find "${backup_dir}" -maxdepth 1 -type f -name 'daily-*.dump.enc' -print | sort -r)
if ((${#old_backups[@]} > 7)); then
  for backup in "${old_backups[@]:7}"; do rm -f -- "${backup}" "${backup}.sha256"; done
fi
echo "Created ${destination}"
