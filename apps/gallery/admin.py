from django.contrib import admin

from .models import Artwork, Category, Favorite, VideoClip


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "category", "is_featured", "is_published", "created_at")
    list_filter = ("category", "is_featured", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "description")


@admin.register(VideoClip)
class VideoClipAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_published", "created_at")
    list_filter = ("is_published",)
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "artwork", "created_at")
    search_fields = ("user__email", "artwork__title")
