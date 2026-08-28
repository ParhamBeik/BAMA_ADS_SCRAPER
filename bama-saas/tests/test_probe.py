import warnings
import pytest
from django.core.cache import CacheKeyWarning, cache
from apps.core.models import Brand, Model


@pytest.mark.django_db
def test_locmem_warns_on_spaces():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cache.get("distribution:kia موتور جدید:None")
    print("WARNINGS:", [str(x.category) for x in w])
    assert any(issubclass(x.category, CacheKeyWarning) for x in w), "locmem does NOT validate"


@pytest.mark.django_db
def test_huge_year(api_client):
    for q in ("?year=99999999999999999999", "?model=99999999999999999999",
              "?variant=-1", "?year=-5", "?brand=" + "x" * 5000):
        r = api_client.get("/api/analytics/distribution/" + q)
        print(q[:40], "->", r.status_code)
        assert r.status_code == 200, q


@pytest.mark.django_db
def test_movement_huge(api_client):
    for q in ("?model=99999999999999999999", "?model=1&year=99999999999999999999"):
        r = api_client.get("/api/analytics/movement/" + q)
        print(q, "->", r.status_code)
