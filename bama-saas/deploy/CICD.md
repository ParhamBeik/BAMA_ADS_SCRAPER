# CI/CD setup

`.github/workflows/ci.yml` runs on every push. `.github/workflows/deploy.yml`
runs on pushes to `main`, calls CI first, and only then tells the VPS to pull and
restart. This file is the one-time setup that makes the deploy half work.

The VPS already holds its own checkout at `/opt/apps/BAMA_ADS_SCRAPER` with a
read-only GitHub deploy key, and `/opt/apps/deploy_bama.sh` already does the
fetch/reset/restart. CI ships no code — it only triggers that script.

## 1. A deploy user that can do exactly one thing

`bama-deploy`, not the pre-existing shared `deploy` account: that one is in the
`docker` group, and access to the Docker socket is root — it can mount the host
filesystem into a container. It also already carries an unrestricted key for
another project. A dedicated account keeps the blast radius of a leaked CI key
to "can redeploy bama".

Run on the VPS as root:

```sh
adduser --disabled-password --gecos "" bama-deploy   # deliberately NOT in `docker`
mkdir -p /home/bama-deploy/.ssh
chmod 700 /home/bama-deploy/.ssh
chown -R bama-deploy:bama-deploy /home/bama-deploy/.ssh

# The only privilege this account gets.
echo 'bama-deploy ALL=(root) NOPASSWD: /opt/apps/deploy_bama.sh' > /etc/sudoers.d/bama-deploy
chmod 440 /etc/sudoers.d/bama-deploy
visudo -c          # must print "parsed OK" before you log out
```

## 2. A key that can only run the deploy

Generate locally (never on the VPS — the private half should not exist there):

```sh
ssh-keygen -t ed25519 -f ~/.ssh/bama_ci_deploy -C "github-actions-deploy" -N ""
```

Install the public half on the VPS. The forced command is the security boundary:
whatever GitHub sends is ignored and only this script runs, so a stolen key
cannot open a shell, forward a port, or reach Postgres.

```sh
cat > /home/bama-deploy/.ssh/authorized_keys <<'EOF'
command="sudo /opt/apps/deploy_bama.sh",no-pty,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,restrict ssh-ed25519 AAAA... github-actions-bama-deploy
EOF
chmod 600 /home/bama-deploy/.ssh/authorized_keys
chown bama-deploy:bama-deploy /home/bama-deploy/.ssh/authorized_keys
```

Verify from your laptop using the current VPS host. Either command invokes the
forced deploy, so run these only when a release is authorized. Set `VPS_HOST`
from your verified server inventory first:

```sh
ssh -i ~/.ssh/bama_ci_deploy "bama-deploy@$VPS_HOST"
ssh -i ~/.ssh/bama_ci_deploy "bama-deploy@$VPS_HOST" whoami
```

`/opt/apps/deploy_bama.sh` sets `GIT_SSH_COMMAND` explicitly rather than relying
on `/root/.ssh/config`. `sudo` does not reset `HOME`, so run as `bama-deploy`
the wrapper's `git fetch` would look for the GitHub deploy key under
`/home/bama-deploy` and fail to authenticate.

## 3. Repository secrets

Set `VPS_HOST` to the verified VPS hostname or address and `PUBLIC_ORIGIN` to
the trusted HTTPS origin. Check both before running these commands:

```sh
: "${VPS_HOST:?set the current VPS host}"
: "${PUBLIC_ORIGIN:?set the trusted HTTPS origin}"
gh secret set VPS_SSH_KEY   < ~/.ssh/bama_ci_deploy
gh secret set VPS_HOST      --body "$VPS_HOST"
gh secret set VPS_USER      --body "bama-deploy"
gh secret set VPS_HEALTH_URL --body "$PUBLIC_ORIGIN"

# Verify the host key fingerprint through an independent trusted channel before
# setting VPS_HOST_KEY. A bare ssh-keyscan result does not establish identity.
```

## 4. First run

`deploy.yml` runs on `push: branches: [main]` and can also be triggered from the
Actions tab. After a run, confirm:

```sh
ssh "$VPS_HOST" 'git -C /opt/apps/BAMA_ADS_SCRAPER log --oneline -1'
ssh "$VPS_HOST" 'docker ps --filter name=bama --format "{{.Names}}\t{{.Status}}"'
```

The checkout and each running image must match the intended release. Verify all
six BAMA containers; worker and ML have no container health checks, so inspect
their recent persisted jobs too.

## Public origin cutover

The BAMA origin is `https://bama.parhambm.ir`. Set `ALLOWED_HOSTS`,
`CORS_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, and `VITE_SITE_URL` as in
`.env.production.example`; keep the local backend hosts needed by container
health checks. In the shared `/opt/apps/vps-edge/Caddyfile`, change only the
`http://bama.parhambm.ir` site to a redirect:

```caddyfile
http://bama.parhambm.ir {
    redir https://bama.parhambm.ir{uri} 308
}
```

Validate the complete Caddyfile before reloading it. Check that HTTP returns
308, HTTPS passes certificate verification and both API health endpoints return
200, then verify login and CSRF over HTTPS from an Iranian network. Once the
deploy health URL uses HTTPS, remove the BAMA `:8082` HTTP listener and change
the portal's BAMA link to the trusted origin. That listener also strips Secure
from cookies, so it must not remain a public authenticated fallback. Check the
Portfolio and News routes after the Caddy reload. Keep the old Caddyfile and
production env for rollback; this edge is shared with other applications.

## Rolling back

The VPS checkout is a normal git repo, so a rollback is a deploy of an older ref:

```sh
: "${VPS_HOST:?set the current VPS host}"
: "${GOOD_SHA:?set the reviewed 40-character commit SHA}"
case "$GOOD_SHA" in *[!0-9a-f]* | "") echo "invalid commit SHA" >&2; exit 1 ;; esac
ssh "$VPS_HOST" "cd /opt/apps/BAMA_ADS_SCRAPER && git reset --hard $GOOD_SHA \
  && bash bama-saas/deploy/vps_pull_deploy.sh"
```

Note that `deploy_bama.sh` resets to `origin/main`, so the next push undoes a
manual rollback. Revert the commit on `main` for anything longer than a hotfix.
