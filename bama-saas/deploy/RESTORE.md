# Restore an encrypted Postgres backup

Backups are written by [`backup_postgres.sh`](backup_postgres.sh) to
`/var/backups/bama/daily-YYYY-MM-DD.dump.enc` (AES-256-CBC, pbkdf2, 310000
iterations). Keep `BACKUP_PASSPHRASE_FILE` mode 400/600 **outside** the repo.

Production runs `docker-compose.prod.yml`. The restore below uses the local
compose file as a practice target; against the VPS, swap in
`docker-compose.prod.yml` and `--env-file .env.production`, and stop `django`,
`worker`, `ml` and `frontend` instead of the local service names.

## Restore into a new scratch database

The practice target must be a new database. Do not stop the running app or use
`--clean`: those commands would replace the existing database, not rehearse a
restore. Run this in Bash, with a backup and its matching passphrase:

```bash
set -euo pipefail
project_dir=/path/to/bama-saas
passphrase_file=/path/outside/repository/bama-backup-passphrase
dump=/path/to/daily-YYYY-MM-DD.dump.enc
scratch_db="bama_restore_$(date +%Y%m%d_%H%M%S)"
compose=(docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml")

# createdb refuses an existing name; the restore cannot overwrite live data.
"${compose[@]}" exec -T postgres createdb -U postgres "$scratch_db"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
  -pass "file:${passphrase_file}" -in "$dump" \
  | "${compose[@]}" exec -T postgres pg_restore \
      --exit-on-error --no-owner --no-acl -U postgres -d "$scratch_db"

"${compose[@]}" exec -T postgres psql -U postgres -d "$scratch_db" \
  -c 'SELECT count(*) FROM accounts_user' \
  -c 'SELECT count(*) FROM catalog_ad' \
  -c 'SELECT count(*) FROM django_migrations'
```

Compare counts with those recorded when the backup was taken. Point an isolated
Django process at the scratch database and run `manage.py check` and
`manage.py migrate --check`. Keep scheduled workers pointed at their original
database. Leave the scratch database in place until its results are reviewed.

A production replacement is a separate, explicitly authorized operation: stop
all writers including the ML trainer, preserve the current database, restore,
verify schema and application reads, then restart writers.

`pg_restore --list` checks the archive catalogue only. A successful full restore
plus schema and data checks is the recovery evidence; decrypting or listing an
archive alone is insufficient.
