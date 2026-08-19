"""Catalog dimension + Ad snapshot serializers (read-only)."""

from rest_framework import serializers

from apps.core.models import Ad, Brand, City, Dealer, Model, Variant
from apps.core.services.listing_kind import condition_discounted, price_basis_unclear


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
    dealer_name = serializers.CharField(source="dealer.name", read_only=True, default=None)
    seller_type = serializers.SerializerMethodField()
    # Verdicts, not raw data: a listing the cohort pass could not believe should
    # say so to whoever is looking at it rather than quietly leaving the market
    # statistics. See apps/jobs/services/verify_cohort.py.
    cohort_flags = serializers.JSONField(read_only=True)
    # Why this listing's price may not be comparable to its cohort's. Both are
    # derived from text already loaded on the row, so neither costs a query.
    # The deal board *excludes* the first and only *labels* the second — see
    # apps/core/services/listing_kind.py for why that asymmetry is deliberate.
    price_basis_unclear = serializers.SerializerMethodField()
    condition_flagged = serializers.SerializerMethodField()

    def get_seller_type(self, obj) -> str:
        return "dealer" if obj.dealer_id is not None else "private"

    def get_price_basis_unclear(self, obj) -> bool:
        return price_basis_unclear(
            title=obj.title,
            description=obj.description,
            price_type=obj.price_type,
            prepayment=obj.current_prepayment,
        )

    def get_condition_flagged(self, obj) -> bool:
        return condition_discounted(title=obj.title, description=obj.description)

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
            "body_type",
            "fuel",
            "city_id",
            "city_name",
            "url",
            "description",
            "primary_image_url",
            "image_urls",
            "image_count",
            "seller_authenticated",
            "dealer_name",
            "seller_type",
            "year_jalali",
            "status",
            "cohort_flags",
            "price_basis_unclear",
            "condition_flagged",
        )
        # raw_payload is deliberately absent. It is the entire scraped record —
        # dealer contact details, internal identifiers, promotion state, every
        # field the source ever sent — and it was being served on the public list
        # endpoint, which also made every ad response many times larger than the
        # curated fields anyone actually reads. It remains available to staff at
        # /api/admin/ads/<code>/provenance/.
