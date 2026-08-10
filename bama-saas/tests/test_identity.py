"""Listing episodes and physical-car identity.

Test type: integration — episodes are derived from stored Ad state and identity
links span rows, so neither exists without the database.

The load-bearing test is ``test_a_reused_image_folder_on_a_different_car_is_refused``.
A false merge fuses two cars' price and lifecycle histories into one, and every
table built on top of it inherits the error, so the cost is asymmetric: a missed
link is a smaller number, a wrong link is a wrong number nobody can trace back.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import Ad, Brand, City, ListingEpisode, Model, Variant, VehicleIdentity
from apps.jobs.services import identity as I

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
UUID_A = "27d7b382-032b-4f24-aa25-dce5d9db8c98"
UUID_B = "7079a1f9-8858-4b73-8f0a-92103720d892"


def payload_with_images(uuid, n=2):
    base = f"https://cdn-sth1.bama.ir/uploads/BamaImages/VehicleCarImages/{uuid}"
    return {
        "images": [
            {
                "large": f"{base}/CarImage_{i}_thumb_900_600.jpg?x-img=v1/resize,w_600",
                "thumb": f"{base}/CarImage_{i}_thumb_900_600.jpg?x-img=v1/resize,w_240",
            }
            for i in range(n)
        ]
    }


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    other = Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    return {
        "brand": brand, "model": model, "other_model": other,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
    }


def make_ad(catalog, code, *, uuid=UUID_A, status=Ad.Status.ACTIVE, first_seen=NOW,
            last_seen=NOW, removed_at=None, price=1_850_000_000, model=None, year=1401):
    return Ad.objects.create(
        code=code, brand=catalog["brand"], model=model or catalog["model"],
        variant=catalog["variant"], city=catalog["city"],
        year_jalali=year, mileage=44_000, current_price=price, status=status,
        first_seen_at=first_seen, last_seen_at=last_seen, removed_at=removed_at,
        publish_at=first_seen,
        raw_payload=payload_with_images(uuid) if uuid else {},
    )


# --- the identity key -------------------------------------------------------

def test_the_key_is_the_vehicle_folder_not_the_file_or_transform():
    """Same car, different renditions: file name, size suffix and ?x-img=
    transform parameters all vary between requests for one photo."""
    a = {"images": [{"large": f"https://cdn.bama.ir/uploads/BamaImages/VehicleCarImages/{UUID_A}/CarImage_1_thumb_900_600.jpg?x-img=v1/resize,w_600"}]}
    b = {"images": [{"thumb": f"https://cdn-sth9.bama.ir/uploads/BamaImages/VehicleCarImages/{UUID_A}/CarImage_9_thumb_120_90.jpg?x-img=v1/format,type_webp/resize,w_120"}]}

    assert I.image_identity_key(a) == I.image_identity_key(b) == UUID_A


def test_no_photos_means_no_evidence():
    """None, never a shared placeholder — an identity built on absence would
    merge every photoless listing in the market into a single car."""
    assert I.image_identity_key({}) is None
    assert I.image_identity_key({"images": []}) is None
    assert I.image_identity_key({"images": [{"large": "https://x/y.jpg"}]}) is None


# --- episodes ---------------------------------------------------------------

@pytest.mark.django_db
def test_an_active_ad_opens_an_episode(catalog):
    make_ad(catalog, "ep000001")
    I.sync_episodes()

    episode = ListingEpisode.objects.get(ad_id="ep000001")
    assert episode.is_open
    assert episode.started_at == NOW


@pytest.mark.django_db
def test_a_removed_ad_closes_its_episode(catalog):
    make_ad(catalog, "ep000002")
    I.sync_episodes()
    Ad.objects.filter(code="ep000002").update(
        status=Ad.Status.REMOVED, removed_at=NOW + timedelta(days=3)
    )
    I.sync_episodes()

    episode = ListingEpisode.objects.get(ad_id="ep000002")
    assert episode.ended_at == NOW + timedelta(days=3)


@pytest.mark.django_db
def test_a_reappearance_opens_a_second_episode(catalog):
    """Not a resurrection of the first: the gap between them is the interesting
    part, and merging the two would report one long listing that was never
    continuously available."""
    make_ad(catalog, "ep000003")
    I.sync_episodes()
    Ad.objects.filter(code="ep000003").update(
        status=Ad.Status.REMOVED, removed_at=NOW + timedelta(days=2)
    )
    I.sync_episodes()
    Ad.objects.filter(code="ep000003").update(
        status=Ad.Status.ACTIVE, removed_at=None, last_seen_at=NOW + timedelta(days=9)
    )
    I.sync_episodes()

    episodes = ListingEpisode.objects.filter(ad_id="ep000003").order_by("started_at")
    assert episodes.count() == 2
    assert episodes.first().ended_at is not None
    assert episodes.last().is_open


@pytest.mark.django_db
def test_sync_is_idempotent(catalog):
    make_ad(catalog, "ep000004")
    I.sync_episodes()
    I.sync_episodes()
    I.sync_episodes()

    assert ListingEpisode.objects.filter(ad_id="ep000004").count() == 1


# --- identity ---------------------------------------------------------------

@pytest.mark.django_db
def test_two_codes_sharing_photos_share_one_identity(catalog):
    """Measured on the live database: 65 image folders covered 139 listing codes,
    and in every inspected case model, year, mileage and price agreed exactly."""
    make_ad(catalog, "twin0001")
    make_ad(catalog, "twin0002")
    I.sync_episodes()

    identities = set(
        ListingEpisode.objects.values_list("identity_id", flat=True)
    )
    assert len(identities) == 1 and None not in identities


@pytest.mark.django_db
def test_different_cars_do_not_share_an_identity(catalog):
    make_ad(catalog, "solo0001", uuid=UUID_A)
    make_ad(catalog, "solo0002", uuid=UUID_B)
    I.sync_episodes()

    assert VehicleIdentity.objects.count() == 2


@pytest.mark.django_db
def test_a_reused_image_folder_on_a_different_car_is_refused(catalog):
    """The asymmetry that governs this whole module.

    A missed link understates relisting — a smaller number. A false link fuses
    two cars' price and lifecycle histories, and every table built on top
    inherits the error with no way to trace it back. So when the uuid says one
    thing and the model says another, the ad is left unlinked.
    """
    make_ad(catalog, "real0001", uuid=UUID_A)
    I.sync_episodes()
    make_ad(catalog, "wrong001", uuid=UUID_A, model=catalog["other_model"])
    I.sync_episodes()

    assert ListingEpisode.objects.get(ad_id="wrong001").identity_id is None
    assert ListingEpisode.objects.get(ad_id="real0001").identity_id is not None


@pytest.mark.django_db
def test_a_photoless_ad_is_left_unlinked(catalog):
    make_ad(catalog, "nopic001", uuid=None)
    I.sync_episodes()

    assert ListingEpisode.objects.get(ad_id="nopic001").identity_id is None


# --- what a shared identity means ------------------------------------------

@pytest.mark.django_db
def test_overlapping_episodes_are_duplicates_not_relists(catalog):
    """The dominant pattern in the live data: the same car listed twice at once,
    which overstates supply rather than restarting any clock."""
    make_ad(catalog, "dup00001")
    make_ad(catalog, "dup00002")
    I.sync_episodes()

    summary = I.identity_summary(VehicleIdentity.objects.get().pk)
    assert summary["duplicated"] is True
    assert summary["relisted"] is False


@pytest.mark.django_db
def test_sequential_episodes_are_relists(catalog):
    """Without linking these, the tenure clock restarts and one delisting is
    counted that never happened — biasing every survival curve toward 'sells
    fast'."""
    make_ad(catalog, "rel00001", status=Ad.Status.REMOVED,
            first_seen=NOW - timedelta(days=40), last_seen=NOW - timedelta(days=30),
            removed_at=NOW - timedelta(days=30))
    make_ad(catalog, "rel00002", first_seen=NOW - timedelta(days=5),
            last_seen=NOW)
    I.sync_episodes()

    summary = I.identity_summary(VehicleIdentity.objects.get().pk)
    assert summary["relisted"] is True
    assert summary["listing_count"] == 2
