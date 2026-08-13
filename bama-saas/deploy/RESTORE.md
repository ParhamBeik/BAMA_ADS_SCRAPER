# Restore an encrypted Postgres backup

Backups are written by [`backup_postgres.sh`](backup_postgres.sh) to
`/var/backups/bama/daily-YYYY-MM-DD.dump.enc` (AES-256-CBC, pbkdf2, 310000
iterations). Keep `BACKUP_PASSPHRASE_FILE` mode 400/600 **outside** the repo.

This stack is local Docker Compose only. There is no prod compose file, no
`.env.production`, and no public domain.

## Restore to a throwaway volume (practice this once)

```bash
project_dir=/path/to/bama-saas          # the Django project, not the git root
passphrase_file=/root/bama-backup-passphrase
dump=/var/backups/bama/daily-YYYY-MM-DD.dump.enc
compose=(docker compose --project-directory $project_dir -f $project_dir/docker-compose.yml)

# 1. Stop writers
"${compose[@]}" stop django worker frontend

# 2. Decrypt and restore (destroys current DB contents)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 310000 \
  -pass "file:${passphrase_file}" -in "$dump" \
  | "${compose[@]}" exec -T postgres pg_restore \
      --clean --if-exists --no-owner --no-acl \
      -U postgres -d bama_saas

# 3. Bring the app back
"${compose[@]}" start django worker frontend
curl -fsS "http://localhost:8001/api/db/health/"
```

`pg_restore --list` (used by the backup script) only verifies the dump is
readable; it does not load data. A real restore uses `--clean` as above.

Rollback of a bad local change is: restore yesterday's dump, then
`"${compose[@]}" up -d`.
