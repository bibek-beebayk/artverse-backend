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

    def get_image(self, obj: Product):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None

    def get_thumbnail(self, obj: Product):
        if not obj.thumbnail:
            return None
        try:
            return obj.thumbnail.url
        except Exception:
            return None

    def _available_variants(self, obj: Product):
        # Relies on the view prefetching `variants` filtered/ordered appropriately to avoid N+1;
        # falls back to a live query if accessed without that prefetch (e.g. in the admin/shell).
        # Deliberately just `is_available` — this is the DISPLAY list (which colours/sizes a
        # shopper can pick), not the stricter "sellable" definition (which also requires a
        # configured production cost and template match) used for starting_price/is_available
        # below. A variant missing its cost still shows as a pickable option; adding it to the
        # cart is where the existing pricing-warning system (apps.cart) catches that.
        if hasattr(obj, "_prefetched_objects_cache") and "variants" in obj._prefetched_objects_cache:
            return [v for v in obj.variants.all() if v.is_available]
        return list(obj.variants.filter(is_available=True))

    def get_variants(self, obj: Product):
        return ProductVariantSerializer(self._available_variants(obj), many=True).data

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
