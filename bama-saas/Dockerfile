FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# libgomp1 is LightGBM's OpenMP runtime. It is a shared library, not a Python
# wheel, so pip cannot supply it and the import fails at runtime without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from pyproject.toml — one list, no requirements.txt to drift
# out of sync with it. Read out with stdlib tomllib rather than `pip install .`
# so this layer caches independently of the source below.
#
# `ml` is in here because the training service shares this image tag and runs a
# different command; one image means one build to verify. It is still its own
# extra so a host that wants the web app without ~120MB of scikit-learn and
# LightGBM can have it — `apps/ml` degrades to a refusal rather than an
# ImportError when they are absent.
#
# `test` is deliberately NOT installed. config/settings.py selects the permissive
# profile when `pytest` is in `sys.modules` — AllowAny on every endpoint,
# ALLOWED_HOSTS `*`, no HTTPS redirect, no throttles — and this image never runs
# the suite (CI runs it on the runner, against a service container). Shipping the
# import here put the trigger for that switch one stray module-level `import
# pytest` away from a live process, in an image that has no use for it.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && python -c "import tomllib, pathlib; p = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']; x = p['optional-dependencies']; print('\n'.join(p['dependencies'] + x['ml']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY . /app/

# Not root. The application never needs to write anywhere except the two named
# volumes, so nothing here has a reason to hold uid 0 — and this VPS runs three
# other stacks beside this one.
#
# The uid is pinned rather than left to `useradd` because it has to match what
# the volumes are owned by. Docker fixes a named volume's ownership once, when
# it first creates it from the image's directory, and does **not** re-chown an
# existing one on `up` — so the two volumes on the VPS were chowned to
# 10001:10001 ahead of this change (2026-09-04). Root can write into a
# 10001-owned directory, so the then-running root containers were unaffected and
# the switch takes effect only on the next deploy. Reversing means `chown -R
# 0:0` on both, in the same way.
#
#   docker run --rm -v bama-saas_staticfiles:/v alpine chown -R 10001:10001 /v
#   docker run --rm -v bama-saas_ml_artifacts:/v alpine chown -R 10001:10001 /v
#
# `--create-home` is load-bearing: gunicorn opens a control socket under
# `$HOME/.gunicorn/`, which was `/root/.gunicorn/gunicorn.ctl` before this and
# has nowhere to go for a user without a home directory.
#
# The source stays root-owned and world-readable — the app reads it and must not
# be able to rewrite the code it is running.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000
# `--access-logfile -` because without it there is no record that a request ever
# happened. "Has anyone ever called this endpoint?" and "did that 500 hit a real
# user?" were both unanswerable on the deployed stack — the container held 39
# lines, all of them boot messages. Traffic here is tens of requests a day, so
# the volume is nothing against the compose logging cap (20m x 5).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--worker-class", "gthread", "--threads", "8", "--access-logfile", "-"]
