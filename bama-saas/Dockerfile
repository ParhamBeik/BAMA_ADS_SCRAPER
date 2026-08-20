FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies come from pyproject.toml — one list, no requirements.txt to drift
# out of sync with it. Read out with stdlib tomllib rather than `pip install .`
# so this layer caches independently of the source below.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && python -c "import tomllib, pathlib; p = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['test']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY . /app/

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
