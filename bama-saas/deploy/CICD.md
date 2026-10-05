# CI/CD setup

The four apps on this VPS deploy the same way:

```
PR -> ci.yml on GitHub -> merge to main -> deploy.yml builds the images on GitHub
   -> ghcr.io/parhambeik/bama_ads_scraper-{app,frontend}:<sha>
   -> the VPS runner runs `sudo app-release bama <sha>` -> public health probes
   -> on failure, the previous release is redeployed
```

Why it is split this way:

- **Images build on GitHub.** The VPS cannot reach `files.pythonhosted.org`, so
  `pip install` inside an on-box build fails without a mirror, and a build competes
  with three other stacks for RAM.
- **The deploy runs on a self-hosted runner.** GitHub's hosted runners cannot
  reach this VPS: their SSH connections never arrive (Iranian network filtering).
  The VPS reaches GitHub fine, so the runner dials out and collects the job.
- **The runner has no Docker access.** It runs as `gh-runner`, which is not in the
  `docker` group, because Docker socket access is root. Its only sudo right is
  `/usr/local/sbin/app-release`. That root-owned wrapper refuses any SHA not on
  `origin/main`, checks it out in `/opt/apps/BAMA_ADS_SCRAPER`, runs
  `bama-saas/deploy/vps_pull_deploy.sh` with `IMAGE_TAG=<sha>`, and records the
  SHA in `.release`. The repo is public, so every fork PR's workflows also need
  approval (`fork-pr-contributor-approval` = `all_external_contributors`).

## Runner

One runner per repository, all under `/opt/gh-runners/<app>` as `gh-runner`,
label `vps`. Re-register with a one-hour token piped from your laptop:

```sh
gh api -X POST repos/ParhamBeik/BAMA_ADS_SCRAPER/actions/runners/registration-token -q .token |
  ssh root@"$VPS_HOST" 'read -r T; cd /opt/gh-runners/bama &&
    sudo -u gh-runner ./config.sh --unattended --replace --name vps-bama --labels vps \
      --url https://github.com/ParhamBeik/BAMA_ADS_SCRAPER --token "$T" &&
    ./svc.sh install gh-runner && ./svc.sh start'
gh api repos/ParhamBeik/BAMA_ADS_SCRAPER/actions/runners -q '.runners[]|"\(.name) \(.status)"'
```

## Repository secrets

None. `VPS_HEALTH_URL` is no longer read; the public
origin is in `deploy.yml`.

## Checking a deploy

```sh
ssh "$VPS_HOST" 'cat /opt/apps/BAMA_ADS_SCRAPER/.release'
ssh "$VPS_HOST" 'docker ps --filter name=bama --format "{{.Names}}\t{{.Status}}"'
```

Verify all six BAMA containers; worker and ML have no container health checks,
so inspect their recent persisted jobs too.

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

Actions -> Deploy -> Run workflow, with the full SHA of an earlier `main` commit.
Its images are already in GHCR, so nothing is rebuilt. This does not reverse
migrations: an older release runs against the newer schema.
