# Restore an encrypted Postgres backup

Backups are written by [`backup_postgres.sh`](backup_postgres.sh) to
`/var/backups/bama/daily-YYYY-MM-DD.dump.enc` (AES-256-CBC, pbkdf2, 310000
iterations). Keep `BACKUP_PASSPHRASE_FILE` mode 400/600 **outside** the repo.

## Restore to a throwaway volume (practice this once)

```bash
project_dir=/opt/bama-saas          # or wherever the clone lives
env_file=$project_dir/.env.production
passphrase_file=/root/bama-backup-passphrase
dump=/var/backups/bama/daily-YYYY-MM-DD.dump.enc
compose=(docker compose -f $project_dir/deploy/docker-compose.prod.yml \
         --project-directory $project_dir --env-file $env_file)

# 1. Stop writers
"${compose[@]}" stop backend worker frontend

# 2. Decrypt and restore (destroys current DB contents)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
  -pass "file:${passphrase_file}" -in "$dump" \
  | "${compose[@]}" exec -T db pg_restore \
      --clean --if-exists --no-owner --no-acl \
      -U "$POSTGRES_USER" -d "$POSTGRES_DB"

# 3. Bring the app back
"${compose[@]}" start backend worker frontend
curl -fsS "https://${BAMA_DOMAIN}/api/db/health/"
```

`pg_restore --list` (used by the backup script) only verifies the dump is
readable; it does not load data. A real restore uses `--clean` as above.

Rollback of a bad deploy is: restore yesterday's dump, then
`"${compose[@]}" up -d` with the previous image tags if you tagged them.
The edge network (`vps-edge`) is left unchanged.
