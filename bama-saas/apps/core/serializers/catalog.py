"""Catalog dimension + Ad snapshot serializers (read-only)."""

from rest_framework import serializers

from apps.core.models import Ad, Brand, City, Dealer, Model, Variant


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ("slug", "name_fa", "name_en", "aliases")


class ModelSerializer(serializers.ModelSerializer):
    brand_slug = serializers.SlugRelatedField(
        source="brand", slug_field="slug", read_only=True
    )

    class Meta:
        model = Model
        fields = ("id", "brand_slug", "name_fa")


class VariantSerializer(serializers.ModelSerializer):
    model_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Variant
        fields = ("id", "model_id", "name_fa")


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ("id", "name_fa", "province")


class DealerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dealer
        fields = (
            "id",
            "name",
            "type",
            "package_type",
            "score",
            "ad_count",
            "address",
            "link",
            "logo",
        )


class AdSerializer(serializers.ModelSerializer):
    """Flat current-snapshot of an ad.

    Brand is referenced by its natural value (slug); model/variant/city/dealer
    by id. `raw_payload` is exposed verbatim as nested JSON (read-only).
    """

    brand_slug = serializers.SlugRelatedField(
        source="brand", slug_field="slug", read_only=True
    )
    brand_name = serializers.CharField(source="brand.name_fa", read_only=True)
    model_id = serializers.IntegerField(read_only=True)
    model_name = serializers.CharField(source="model.name_fa", read_only=True, default="")
    variant_id = serializers.IntegerField(read_only=True)
    variant_name = serializers.CharField(
        source="variant.name_fa", read_only=True, default=""
    )
    city_id = serializers.IntegerField(read_only=True)
    city_name = serializers.CharField(source="city.name_fa", read_only=True, default="")
    # Verdicts, not raw data: a listing the cohort pass could not believe should
    # say so to whoever is looking at it rather than quietly leaving the market
    # statistics. See apps/jobs/services/verify_cohort.py.
    cohort_flags = serializers.JSONField(read_only=True)

    class Meta:
        model = Ad
        fields = (
            "code",
            "brand_slug",
            "brand_name",
            "model_id",
            "model_name",
            "variant_id",
            "variant_name",
            "year",
            "mileage",
            "current_price",
            "price_type",
            "publish_at",
            "last_seen_at",
            "title",
            "transmission",
            "city_id",
            "city_name",
            "url",
            "cohort_flags",
        )
        # raw_payload is deliberately absent. It is the entire scraped record —
        # dealer contact details, internal identifiers, promotion state, every
        # field the source ever sent — and it was being served on the public list
        # endpoint, which also made every ad response many times larger than the
        # curated fields anyone actually reads. It remains available to staff at
        # /api/admin/ads/<code>/provenance/.
