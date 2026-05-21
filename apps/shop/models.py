from django.conf import settings
from django.db import models

from config.image_utils import build_thumbnail_content


class ProductCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    thumbnail = models.ImageField(upload_to="products/thumbnails/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    inventory = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def regenerate_thumbnail(self, *, save: bool = True) -> bool:
        if not self.image:
            if self.thumbnail:
                self.thumbnail.delete(save=False)
                self.thumbnail = None
                if save and self.pk:
                    self.save(update_fields=["thumbnail"])
            return False

        thumbnail_name, thumbnail_content = build_thumbnail_content(self.image)
        self.thumbnail.save(thumbnail_name, thumbnail_content, save=False)
        if save and self.pk:
            self.save(update_fields=["thumbnail"])
        return True

    def save(self, *args, **kwargs):
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
            super().save(update_fields=["thumbnail"])


class NotificationSubscription(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="notification_signups")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_notification_signups",
    )
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("product", "email")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.email} -> {self.product}"
