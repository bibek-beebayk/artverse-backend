from django.conf import settings
from django.db import models

from config.image_utils import build_thumbnail_content
from config.slug_utils import unique_slugify


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to auto-generate a unique slug from the name.")

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)


class Collection(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to auto-generate a unique slug from the name.")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)


class Artwork(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to auto-generate a unique slug from the title.")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="artworks")
    collection = models.ForeignKey(
        Collection,
        on_delete=models.SET_NULL,
        related_name="artworks",
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="artworks/", blank=True, null=True)
    thumbnail = models.ImageField(upload_to="artworks/thumbnails/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title

    def regenerate_thumbnail(self, *, save: bool = True) -> bool:
        if not self.image:
            if self.thumbnail:
                self.thumbnail.delete(save=False)
                self.thumbnail = None
                if save and self.pk:
                    self.save(update_fields=["thumbnail", "updated_at"])
            return False

        thumbnail_name, thumbnail_content = build_thumbnail_content(self.image)
        self.thumbnail.save(thumbnail_name, thumbnail_content, save=False)
        if save and self.pk:
            self.save(update_fields=["thumbnail", "updated_at"])
        return True

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.title)
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = list(update_fields) + ["slug"]

        update_fields = kwargs.get("update_fields")
        should_check_thumbnail = update_fields is None or "image" in update_fields or "thumbnail" in update_fields
        previous_image_name = None

        if self.pk and should_check_thumbnail:
            previous_image_name = type(self).objects.filter(pk=self.pk).values_list("image", flat=True).first()

        super().save(*args, **kwargs)

        if not should_check_thumbnail:
            return

        current_image_name = self.image.name if self.image else ""
        image_changed = previous_image_name != current_image_name
        thumbnail_missing = self.image and not self.thumbnail

        if image_changed or thumbnail_missing:
            self.regenerate_thumbnail(save=True)
        elif not self.image and self.thumbnail:
            self.thumbnail.delete(save=False)
            self.thumbnail = None
            super().save(update_fields=["thumbnail", "updated_at"])


class VideoClip(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to auto-generate a unique slug from the title.")
    thumbnail_url = models.URLField(blank=True)
    video_url = models.URLField()
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.title)
        super().save(*args, **kwargs)


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites")
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "artwork")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user} -> {self.artwork}"
