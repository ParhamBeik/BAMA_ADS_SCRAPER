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
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && python -c "import tomllib, pathlib; p = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']; x = p['optional-dependencies']; print('\n'.join(p['dependencies'] + x['test'] + x['ml']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY . /app/

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
