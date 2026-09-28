#!/usr/bin/env bash
# Pull the persistent VPS photo volume to this Mac, verify bytes, then acknowledge.
set -euo pipefail
umask 077

host="${BAMA_VPS_HOST:-45.139.10.12}"
destination="${BAMA_PHOTO_BACKUP_DIR:-/Users/parham/Backups/BamaPhotos}"
mkdir -p "${destination}/sha256"

remote_root="$(ssh -o BatchMode=yes "${host}" \
  'docker volume inspect -f "{{.Mountpoint}}" bama-saas_photo_archive')"
[[ "${remote_root}" == /var/lib/docker/volumes/bama-saas_photo_archive/_data ]] || {
  echo "Unexpected photo volume path" >&2
  exit 1
}

# Never delete a local backup to mirror a damaged or reset VPS volume.
# Content-addressed files are immutable. Preserve an earlier good Mac copy if
# the VPS file with the same name is later damaged.
rsync -a --ignore-existing --partial-dir=.partial -e 'ssh -o BatchMode=yes' \
  "${host}:${remote_root}/" "${destination}/sha256/"

manifest="$(mktemp "${destination}/.manifest.XXXXXX")"
trap 'rm -f "${manifest}"' EXIT
python3 - "${destination}/sha256" > "${manifest}" <<'PY'
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
sample = None
for path in sorted(root.rglob("*")):
    if ".partial" in path.parts:
        continue
    if path.is_symlink():
        raise SystemExit(f"Symlink in photo backup: {path}")
    if not path.is_file():
        continue
    name = path.name
    if not re.fullmatch(r"[0-9a-f]{64}", name) or path.parent.name != name[:2]:
        raise SystemExit(f"Unexpected photo backup file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != name:
        raise SystemExit(f"Corrupt photo backup file: {path}")
    print(name, path.stat().st_size)
    sample = sample or path

# A separate read-back copy exercises the restore path as well as the checksum.
if sample:
    with tempfile.TemporaryDirectory(dir=root.parent) as temporary:
        restored = Path(temporary) / sample.name
        shutil.copyfile(sample, restored)
        if hashlib.sha256(restored.read_bytes()).hexdigest() != sample.name:
            raise SystemExit("Photo restore sample failed")
PY

ssh -o BatchMode=yes "${host}" \
  'cd /opt/apps/BAMA_ADS_SCRAPER/bama-saas && docker compose -f docker-compose.prod.yml --env-file .env.production exec -T django python manage.py confirm_photo_backup' \
  < "${manifest}"
mv "${manifest}" "${destination}/last-verified.manifest"
trap - EXIT
echo "Verified $(wc -l < "${destination}/last-verified.manifest" | tr -d ' ') photos on this Mac"
