"""Select the test profile before Django is configured.

A ``-p`` plugin rather than a conftest, and that is the whole point. pytest-django
imports ``config.settings`` from inside its own ``pytest_load_initial_conftests``
hook, and plugin hooks run ahead of conftest files in that same hook — so a
rootdir ``conftest.py`` setting these is already too late. ``-p testenv`` (see
``addopts`` in pyproject.toml) is imported during pre-parse, before any of it.

Without this a bare ``pytest`` dies on ``ImproperlyConfigured: SECRET_KEY must be
set when DJANGO_DEBUG is off`` — accurate, and completely unhelpful when what you
typed was "run the tests".

``setdefault``, not assignment: CI sets both explicitly, and the hardened job
passes an empty ``API_PUBLIC_READS`` on purpose to run the suite against the
production permission profile. Overwriting would silently defeat that job.
"""

import os

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("API_PUBLIC_READS", "1")
