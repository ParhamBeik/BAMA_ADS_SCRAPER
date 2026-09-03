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

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--worker-class", "gthread", "--threads", "8"]
