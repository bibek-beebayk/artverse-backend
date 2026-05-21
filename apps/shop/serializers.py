from rest_framework import serializers

from .models import NotificationSubscription, Product, ProductCategory


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("id", "name", "slug")


class ProductSerializer(serializers.ModelSerializer):
    category = ProductCategorySerializer(read_only=True)
    image = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

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


class NotificationSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSubscription
        fields = ("id", "product", "email", "created_at")
        read_only_fields = ("id", "created_at")
