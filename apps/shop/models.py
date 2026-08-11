from django.conf import settings
from django.db import models

from config.image_utils import build_thumbnail_content
from config.slug_utils import unique_slugify


class ProductCategory(models.Model):
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


class Product(models.Model):
    """The Artverse storefront listing — not physical stock. Pricing, availability and
    inventory are entirely variant-driven now (see `ProductVariant` in `apps.generator.models`
    and `apps.shop.services`): there is deliberately no `price`/`inventory` field here anymore.
    `is_active` is the only admin-editable availability switch; whether the product actually has
    anything sellable is a *derived* fact (`product_has_sellable_variant()`), not a second field
    to keep in sync by hand — see `apps.shop.services.validate_product_can_be_activated()` for
    the rule that's supposed to keep `is_active=True` and "no sellable variant" from coexisting."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True, help_text="Leave blank to auto-generate a unique slug from the name.")
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    thumbnail = models.ImageField(upload_to="products/thumbnails/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=False)
    mockup_template = models.ForeignKey(
        "generator.MockupTemplate",
        on_delete=models.SET_NULL,
        related_name="shop_products",
        null=True,
        blank=True,
        help_text="The customizable mockup template used to render this product's previews and print files.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
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
