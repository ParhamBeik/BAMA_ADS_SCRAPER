"""Read-only serializers. Everything else in this app returns plain dicts."""

from rest_framework import serializers

from apps.core import images
from apps.core.models import Ad, Brand, Model, NotifierSettings, Variant
from apps.core.pricing import MIN_PEERS
from apps.core.quality import condition_discounted
from apps.jobs.parsing import absolute_ad_url
from apps.jobs.verify import MAX_PLAUSIBLE_MILEAGE


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ("slug", "name_fa", "name_en", "aliases")


class ModelSerializer(serializers.ModelSerializer):
    brand_slug = serializers.SlugRelatedField(source="brand", slug_field="slug", read_only=True)

    class Meta:
        model = Model
        fields = ("id", "brand_slug", "name_fa")


class VariantSerializer(serializers.ModelSerializer):
    model_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Variant
        fields = ("id", "model_id", "name_fa")


class AdSerializer(serializers.ModelSerializer):
    """Flat current snapshot of an ad.

    ``raw_payload`` is deliberately absent: it is the entire scraped record —
    dealer contact details, internal identifiers, every field the source ever
    sent — and serving it made each response many times larger than the curated
    fields anyone reads. Staff can still get it from
    ``/api/admin/ads/<code>/provenance/``.
    """

    brand_slug = serializers.SlugRelatedField(source="brand", slug_field="slug", read_only=True)
    brand_name = serializers.CharField(source="brand.name_fa", read_only=True)
    model_id = serializers.IntegerField(read_only=True)
    model_name = serializers.CharField(source="model.name_fa", read_only=True, default="")
    variant_id = serializers.IntegerField(read_only=True)
    variant_name = serializers.CharField(source="variant.name_fa", read_only=True, default="")
    city_id = serializers.IntegerField(read_only=True)
    city_name = serializers.CharField(source="city.name_fa", read_only=True, default="")
    dealer_name = serializers.CharField(source="dealer.name", read_only=True, default=None)
    seller_type = serializers.SerializerMethodField()
    cohort_flags = serializers.JSONField(read_only=True)
    # Why this listing's price may not be comparable to its cohort's. Both are
    # derived from text already on the row, so neither costs a query. The deal
    # board *excludes* the first and only *labels* the second — see
    # apps/core/quality.py for why that asymmetry is deliberate.
    condition_flagged = serializers.SerializerMethodField()
    # An odometer nobody can believe — 9,000,000 km on a one-year-old Koleos,
    # 6,900,000 on a Shahin. `verify` already flags these softly and `pricing`
    # already declines to adjust on them, but every screen still printed the
    # number as a fact and "most mileage first" ranked the typos to the top.
    # Same treatment as the two flags above: label the row, never hide it.
    mileage_implausible = serializers.SerializerMethodField()
    # Photos are served from our own origin (apps/core/images.py), not hotlinked
    # from a CDN that blocks us periodically.
    image_url = serializers.SerializerMethodField()
    image_urls = serializers.SerializerMethodField()
    # The ad on bama.ir. `Ad.url` holds a site-relative PATH, so anything
    # rendering it straight into an href resolves against our own origin and
    # dead-ends inside the SPA. Derived here rather than migrated so the ~21k
    # rows already stored are correct immediately.
    bama_url = serializers.SerializerMethodField()

    def get_seller_type(self, obj) -> str:
        return "dealer" if obj.dealer_id is not None else "private"

    def get_image_url(self, obj) -> str:
        return images.ad_image_paths(obj)[0]

    def get_image_urls(self, obj) -> list:
        return images.ad_image_paths(obj)[1]

    def get_bama_url(self, obj) -> str:
        return absolute_ad_url(obj.url or obj.canonical_path)

    def get_mileage_implausible(self, obj) -> bool:
        # The stored column, not a re-derivation: `verify` decided this from the
        # payload at ingest, and the mileage the row carries is what it judged.
        return obj.mileage is not None and not 0 <= obj.mileage <= MAX_PLAUSIBLE_MILEAGE

    def get_condition_flagged(self, obj) -> bool:
        return condition_discounted(
            title=obj.title, description=obj.description,
            body_status=obj.body_status,
        )


    class Meta:
        model = Ad
        fields = (
            "code", "brand_slug", "brand_name", "model_id", "model_name",
            "variant_id", "variant_name", "year", "year_jalali", "mileage",
            "current_price", "price_type", "publish_at", "last_seen_at", "title",
            "transmission", "body_type", "fuel", "city_id", "city_name",
            "district", "body_status",
            "bama_url", "description", "image_url", "image_urls", "image_count",
            "seller_authenticated", "dealer_name", "seller_type", "status",
            "removed_at", "likely_reason", "reason_confidence", "reposted_from",
            # `price_basis_unclear` is the stored column now, not a method: the
            # regex behind it runs at ingest so the browse endpoints can filter
            # on an index instead of scanning every description.
            "cohort_flags", "price_basis_unclear", "condition_flagged",
            "mileage_implausible",
        )


class AdListSerializer(AdSerializer):
    """The grid payload; full prose belongs to the detail endpoint."""

    class Meta(AdSerializer.Meta):
        fields = tuple(field for field in AdSerializer.Meta.fields if field != "description")


class NotifierSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotifierSettings
        fields = ("enabled", "min_discount_pct", "min_peers", "price_min",
                  "price_max", "model_ids", "telegram_chat_id", "updated_at")
        read_only_fields = ("updated_at",)

    def validate_min_discount_pct(self, value):
        # 100% would be a free car; 0 would page you for every listing on site.
        if not 0 < value < 100:
            raise serializers.ValidationError("must be between 0 and 100")
        return value

    def validate_min_peers(self, value):
        # Below the fair-price engine's own floor, the baseline being compared
        # against is not one this app is willing to quote.
        if value < MIN_PEERS:
            raise serializers.ValidationError(
                f"must be at least {MIN_PEERS} — the fair-price engine's peer minimum"
            )
        return value

    def validate_model_ids(self, value):
        if not isinstance(value, list) or any(not isinstance(v, int) for v in value):
            raise serializers.ValidationError("must be a list of model ids")
        return value

    def validate(self, attrs):
        lo = attrs.get("price_min", getattr(self.instance, "price_min", None))
        hi = attrs.get("price_max", getattr(self.instance, "price_max", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({"price_min": "must not exceed price_max"})
        return attrs
