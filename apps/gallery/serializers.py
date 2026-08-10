from rest_framework import serializers

from .models import Artwork, Category, Collection, Favorite, VideoClip


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class CollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = ("id", "name", "slug", "description")


class ArtworkSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    collection = CollectionSerializer(read_only=True)
    image = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = Artwork
        fields = (
            "id",
            "title",
            "slug",
            "category",
            "collection",
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


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")
        read_only_fields = ("id",)


class AdminCollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = ("id", "name", "slug", "description")
        read_only_fields = ("id",)


class AdminArtworkSerializer(serializers.ModelSerializer):
    """Unlike the public ArtworkSerializer above, `category`/`collection` are writable PK fields
    (not nested read-only objects) and `is_published` is exposed — the public serializer never
    needs it since ArtworkListView/ArtworkDetailView already filter to is_published=True."""

    class Meta:
        model = Artwork
        fields = (
            "id",
            "title",
            "slug",
            "category",
            "collection",
            "description",
            "image",
            "thumbnail",
            "image_url",
            "is_featured",
            "is_published",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "thumbnail", "created_at", "updated_at")


class AdminVideoClipSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoClip
        fields = ("id", "title", "slug", "thumbnail_url", "video_url", "is_published", "created_at")
        read_only_fields = ("id", "created_at")


class AdminFavoriteSerializer(serializers.ModelSerializer):
    """Support & Monitoring — read-only, admin-wide."""

    user = serializers.SerializerMethodField()
    artwork_title = serializers.CharField(source="artwork.title", read_only=True)

    class Meta:
        model = Favorite
        fields = ("id", "user", "artwork", "artwork_title", "created_at")
        read_only_fields = fields

    def get_user(self, obj: Favorite):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None
