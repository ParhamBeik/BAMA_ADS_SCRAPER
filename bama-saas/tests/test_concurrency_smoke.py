"""Stdlib concurrency smoke against public browse endpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db(transaction=True)
def test_public_browse_concurrency():
    def hit(_):
        c = APIClient()
        r = c.get("/api/health/")
        return r.status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = [pool.submit(hit, i) for i in range(50)]
        codes = [f.result() for f in as_completed(futs)]
    assert all(code == 200 for code in codes)
