# CI/CD setup

`.github/workflows/ci.yml` runs on every push. `.github/workflows/deploy.yml`
runs on pushes to `main`, calls CI first, and only then has a self-hosted runner
on the VPS pull and restart. This file is the one-time setup that makes the deploy half work.

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

## 2. A self-hosted runner, not inbound SSH

GitHub's hosted runners cannot reach this VPS: their SSH connections never
arrive (Iranian network filtering; checked 2026-10-05 in `journalctl -u ssh`).
The VPS reaches GitHub fine, so a runner on the VPS dials out and collects the
deploy job instead. It runs as `bama-deploy`, so a job gets that account's one
privilege and nothing more.

The repo is public. Before installing, require approval for every fork PR, or a
stranger's PR could add a workflow with `runs-on: self-hosted`:

```sh
gh api -X PUT repos/ParhamBeik/BAMA_ADS_SCRAPER/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=all_external_contributors
```

Install on the VPS as root, taking the version and SHA-256 from the latest
`actions/runner` release notes:

```sh
V=2.337.0 SUM=<sha256 from the release notes>
sudo -u bama-deploy bash -c "mkdir -p ~/actions-runner && cd ~/actions-runner &&
  curl -sSfL -o runner.tgz https://github.com/actions/runner/releases/download/v$V/actions-runner-linux-x64-$V.tar.gz &&
  echo '$SUM  runner.tgz' | sha256sum -c - && tar xzf runner.tgz && rm runner.tgz"
```

Register it with a one-hour token piped from your laptop, so the token is never
printed or stored, then install it as a service:

```sh
gh api -X POST repos/ParhamBeik/BAMA_ADS_SCRAPER/actions/runners/registration-token -q .token |
  ssh root@"$VPS_HOST" 'read -r T; cd /home/bama-deploy/actions-runner &&
    sudo -u bama-deploy ./config.sh --unattended --replace --name bama-vps --labels bama-vps \
      --url https://github.com/ParhamBeik/BAMA_ADS_SCRAPER --token "$T" &&
    ./svc.sh install bama-deploy && ./svc.sh start'
gh api repos/ParhamBeik/BAMA_ADS_SCRAPER/actions/runners -q '.runners[]|"\(.name) \(.status)"'
```

## 3. Repository secrets

Only the smoke test needs one now:

```sh
gh secret set VPS_HEALTH_URL --body "$PUBLIC_ORIGIN"
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
