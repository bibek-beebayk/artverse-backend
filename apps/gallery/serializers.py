from rest_framework import serializers

from .models import Artwork, Category, Favorite, VideoClip


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class ArtworkSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    image = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = Artwork
        fields = (
            "id",
            "title",
            "slug",
            "category",
            "description",
            "image",
            "thumbnail",
            "image_url",
            "is_featured",
            "created_at",
        )

    def get_image(self, obj: Artwork):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None

    def get_thumbnail(self, obj: Artwork):
        if not obj.thumbnail:
            return None
        try:
            return obj.thumbnail.url
        except Exception:
            return None


class VideoClipSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoClip
        fields = ("id", "title", "slug", "thumbnail_url", "video_url", "created_at")


class FavoriteSerializer(serializers.ModelSerializer):
    artwork = ArtworkSerializer(read_only=True)

    class Meta:
        model = Favorite
        fields = ("id", "artwork", "created_at")


class FavoriteToggleSerializer(serializers.Serializer):
    artwork_id = serializers.IntegerField()
