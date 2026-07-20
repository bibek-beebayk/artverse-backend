from rest_framework import serializers

from apps.generator.serializers import ProductVariantSerializer

from .models import NotificationSubscription, Product, ProductCategory
from .services import _sellable_variants_for_product, get_product_starting_price


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("id", "name", "slug")


class ProductSerializer(serializers.ModelSerializer):
    """Product carries no price/inventory of its own anymore — `starting_price`/`is_available`/
    `available_variant_count` are all derived from `ProductVariant` (see apps.shop.services).
    Every derived field here reuses the same prefetch-aware helpers, so calling this serializer
    against a queryset that prefetched `variants` (see ProductListView/ProductDetailView) costs
    zero extra queries per product regardless of how many of these fields are accessed."""

    category = ProductCategorySerializer(read_only=True)
    image = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    mockup_template_id = serializers.IntegerField(read_only=True)
    variants = serializers.SerializerMethodField()
    available_sizes = serializers.SerializerMethodField()
    available_colors = serializers.SerializerMethodField()
    starting_price = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()
    available_variant_count = serializers.SerializerMethodField()
    total_variant_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "category",
            "description",
            "image",
            "thumbnail",
            "image_url",
            "is_active",
            "starting_price",
            "is_available",
            "available_variant_count",
            "total_variant_count",
            "mockup_template_id",
            "variants",
            "available_sizes",
            "available_colors",
        )

    def _fallback_template_image_url(self, obj: Product):
        # A product with no dedicated photo of its own yet (common for a freshly-activated
        # product an admin hasn't photographed) falls back to its linked template's blank
        # garment mockup rather than showing nothing on the storefront. A product with no
        # linked template either has genuinely nothing to show — that's a real content gap,
        # not something to paper over.
        if not obj.mockup_template_id or not obj.mockup_template.base_image:
            return None
        try:
            return obj.mockup_template.base_image.url
        except Exception:
            return None

    def get_image(self, obj: Product):
        if obj.image:
            try:
                return obj.image.url
            except Exception:
                pass
        return self._fallback_template_image_url(obj)

    def get_thumbnail(self, obj: Product):
        if obj.thumbnail:
            try:
                return obj.thumbnail.url
            except Exception:
                pass
        return self._fallback_template_image_url(obj)

    def _available_variants(self, obj: Product):
        # Relies on the view prefetching `variants` filtered/ordered appropriately to avoid N+1;
        # falls back to a live query if accessed without that prefetch (e.g. in the admin/shell).
        # Deliberately just `is_available` (provider/catalogue availability) — used only to
        # derive `available_sizes`/`available_colors` below, i.e. "what can currently be
        # ordered." NOT used for the `variants` field itself — see `_all_product_variants`.
        if hasattr(obj, "_prefetched_objects_cache") and "variants" in obj._prefetched_objects_cache:
            return [v for v in obj.variants.all() if v.is_available]
        return list(obj.variants.filter(is_available=True))

    def _all_product_variants(self, obj: Product):
        # Every variant belonging to this product, available or not, sellable or not. The public
        # `variants` field deliberately includes unavailable/missing-cost/template-mismatched
        # rows (each carrying is_available/is_sellable/pricing_ready via ProductVariantSerializer)
        # so the frontend can render a disabled colour/size option with a reason instead of that
        # combination silently not existing at all.
        if hasattr(obj, "_prefetched_objects_cache") and "variants" in obj._prefetched_objects_cache:
            return list(obj.variants.all())
        return list(obj.variants.all())

    def get_variants(self, obj: Product):
        variants = self._all_product_variants(obj)
        # mockup_template_id in context: obj is already loaded here, so every nested variant's
        # is_sellable check reuses it instead of each variant querying obj.product itself.
        return ProductVariantSerializer(
            variants,
            many=True,
            context={
                **self.context,
                "mockup_template_id": obj.mockup_template_id,
            },
        ).data

    def get_available_sizes(self, obj: Product):
        variants = self._available_variants(obj)
        if variants:
            return sorted({v.size for v in variants if v.size})
        if obj.mockup_template_id:
            return obj.mockup_template.supported_sizes
        return []

    def get_available_colors(self, obj: Product):
        variants = self._available_variants(obj)
        if variants:
            return sorted({v.color_name for v in variants if v.color_name})
        if obj.mockup_template_id:
            return obj.mockup_template.supported_colors
        return []

    def get_starting_price(self, obj: Product):
        price = get_product_starting_price(obj)
        return str(price) if price is not None else None

    def get_is_available(self, obj: Product):
        return len(_sellable_variants_for_product(obj)) > 0

    def get_available_variant_count(self, obj: Product):
        return len(_sellable_variants_for_product(obj))

    def get_total_variant_count(self, obj: Product):
        if hasattr(obj, "_prefetched_objects_cache") and "variants" in obj._prefetched_objects_cache:
            return len(obj.variants.all())
        return obj.variants.count()


class NotificationSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSubscription
        fields = ("id", "product", "email", "created_at")
        read_only_fields = ("id", "created_at")
