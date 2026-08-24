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

Verify from your laptop — it should run the deploy and refuse a shell:

```sh
ssh -i ~/.ssh/bama_ci_deploy bama-deploy@89.106.206.4          # runs the deploy
ssh -i ~/.ssh/bama_ci_deploy bama-deploy@89.106.206.4 whoami   # ignored; runs the deploy
```

`/opt/apps/deploy_bama.sh` sets `GIT_SSH_COMMAND` explicitly rather than relying
on `/root/.ssh/config`. `sudo` does not reset `HOME`, so run as `bama-deploy`
the wrapper's `git fetch` would look for the GitHub deploy key under
`/home/bama-deploy` and fail to authenticate.

## 3. Repository secrets

```sh
gh secret set VPS_SSH_KEY   < ~/.ssh/bama_ci_deploy
gh secret set VPS_HOST      --body "89.106.206.4"
gh secret set VPS_USER      --body "bama-deploy"
gh secret set VPS_HEALTH_URL --body "https://bama-89-106-206-4.sslip.io"

# Pinning the host key is what stops a MITM between the runner and the VPS from
# silently receiving the deploy. Capture it once, from a trusted network:
ssh-keyscan -t ed25519 89.106.206.4 | gh secret set VPS_HOST_KEY
```

## 4. First run

`deploy.yml` runs on `push: branches: [main]` and can also be triggered from the
Actions tab. After a run, confirm:

```sh
ssh 89.106.206.4 'git -C /opt/apps/BAMA_ADS_SCRAPER log --oneline -1'
ssh 89.106.206.4 'docker ps --filter name=bama --format "{{.Names}}\t{{.Status}}"'
```

The checkout should be at the pushed SHA and all four `bama-*` containers healthy.

## Rolling back

The VPS checkout is a normal git repo, so a rollback is a deploy of an older ref:

```sh
ssh 89.106.206.4 'cd /opt/apps/BAMA_ADS_SCRAPER && git reset --hard <good-sha> \
  && bash bama-saas/deploy/vps_pull_deploy.sh'
```

Note that `deploy_bama.sh` resets to `origin/main`, so the next push undoes a
manual rollback. Revert the commit on `main` for anything longer than a hotfix.
