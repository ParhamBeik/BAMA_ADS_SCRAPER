"""Dimension resolver: get_or_create Brand/Model/Variant/City/Dealer.

A process-local cache backs every resolver so a bulk import of ~44k ads does
O(unique values) dimension lookups instead of O(ads). Call ``reset_cache`` at
the start and end of a bulk command.
"""

from __future__ import annotations

import re
from typing import Any

from django.db import IntegrityError
from django.utils.text import slugify

from apps.catalog.models import Brand, City, Dealer, Model, Variant

# Location strings look like "تهران - منطقه ۱" or "بابل"; the first segment is
# the city. Split on common Bama separators.
_LOCATION_SEP = re.compile(r"\s*[-–،,]\s*")

_CACHE: dict = {}


def reset_cache() -> None:
    _CACHE.clear()


def _brand(name: str | None):
    if not name:
        return None
    name = name.strip()
    key = ("brand", name)
    if key in _CACHE:
        return _CACHE[key]
    brand = Brand.objects.filter(name_fa=name).first()
    if brand is None:
        slug = slugify(name, allow_unicode=True) or name
        try:
            brand = Brand.objects.create(name_fa=name, slug=slug)
        except IntegrityError:
            # Slug collided with a different brand name; keep the name unique.
            brand = Brand.objects.filter(name_fa=name).first() or Brand.objects.create(
                name_fa=name, slug=f"{slug}-{name[:40]}"
            )
    _CACHE[key] = brand
    return brand


def _model(brand, name: str | None):
    if not brand or not name:
        return None
    name = name.strip()
    key = ("model", brand.pk, name)
    if key in _CACHE:
        return _CACHE[key]
    model, _ = Model.objects.get_or_create(brand=brand, name_fa=name)
    _CACHE[key] = model
    return model


def _variant(model, name: str | None):
    if not model:
        return None
    name = (name or "default").strip() or "default"
    key = ("variant", model.pk, name)
    if key in _CACHE:
        return _CACHE[key]
    variant, _ = Variant.objects.get_or_create(model=model, name_fa=name)
    _CACHE[key] = variant
    return variant


def _city(location: str | None):
    if not location:
        return None
    first = _LOCATION_SEP.split(location.strip())[0].strip()
    if not first:
        return None
    key = ("city", first)
    if key in _CACHE:
        return _CACHE[key]
    city, _ = City.objects.get_or_create(name_fa=first)
    _CACHE[key] = city
    return city


def _dealer(dealer_data: dict | None):
    if not dealer_data:
        return None
    dealer_id = dealer_data.get("id")
    if not dealer_id:
        return None
    try:
        dealer_id = int(dealer_id)
    except (TypeError, ValueError):
        return None
    key = ("dealer", dealer_id)
    if key in _CACHE:
        return _CACHE[key]
    dealer, _ = Dealer.objects.get_or_create(
        id=dealer_id,
        defaults={
            "name": dealer_data.get("name") or "",
            "type": dealer_data.get("type") or "",
            "package_type": dealer_data.get("package_type") or "",
            "score": dealer_data.get("score"),
            "ad_count": dealer_data.get("ad_count"),
            "address": dealer_data.get("address") or "",
            "link": dealer_data.get("link") or "",
            "logo": dealer_data.get("logo") or "",
        },
    )
    _CACHE[key] = dealer
    return dealer


def resolve_dimensions(
    *,
    brand_name: str | None,
    model_name: str | None,
    trim_name: str | None,
    city_location: str | None,
    dealer: dict | None = None,
) -> dict[str, Any]:
    brand = _brand(brand_name)
    model = _model(brand, model_name)
    variant = _variant(model, trim_name)
    city = _city(city_location)
    dealer_obj = _dealer(dealer)
    return {
        "brand": brand,
        "model": model,
        "variant": variant,
        "city": city,
        "dealer": dealer_obj,
    }
