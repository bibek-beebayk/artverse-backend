from rest_framework import serializers

from apps.generator.serializers import ProductVariantSerializer

from .models import NotificationSubscription, Product, ProductCategory


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("id", "name", "slug")


class ProductSerializer(serializers.ModelSerializer):
    category = ProductCategorySerializer(read_only=True)
    image = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()
    mockup_template_id = serializers.IntegerField(read_only=True)
    variants = serializers.SerializerMethodField()
    available_sizes = serializers.SerializerMethodField()
    available_colors = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "category",
            "description",
            "price",
            "image",
            "thumbnail",
            "image_url",
            "inventory",
            "is_active",
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


class NotificationSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSubscription
        fields = ("id", "product", "email", "created_at")
        read_only_fields = ("id", "created_at")
