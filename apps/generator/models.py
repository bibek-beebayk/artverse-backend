from django.conf import settings
from django.db import models


class GenerationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generation_requests")
    prompt = models.TextField()
    style = models.CharField(max_length=120, blank=True)
    provider = models.CharField(max_length=50, default="gemini")
    model_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user} - {self.prompt[:40]}"


class GeneratedImage(models.Model):
    generation_request = models.ForeignKey(
        GenerationRequest,
        on_delete=models.CASCADE,
        related_name="generated_images",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generated_images")
    prompt = models.TextField()
    image = models.ImageField(upload_to="generated/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user} - generated image"


class SourceDesignAsset(models.Model):
    artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="source_design_assets",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255, blank=True)
    source_url = models.TextField(
        blank=True,
        help_text="Optional original image URL. Only needed when no uploaded image or artwork image is available.",
    )
    source_fingerprint = models.CharField(max_length=64, unique=True, blank=True)
    image = models.ImageField(
        upload_to="mockup-source-assets/",
        blank=True,
        null=True,
        help_text="Leave empty when this asset should inherit the image from the selected artwork.",
    )
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    notes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.title or self.source_fingerprint


class MockupTemplate(models.Model):
    class ProductType(models.TextChoices):
        TSHIRT = "tshirt", "T-Shirt"
        HOODIE = "hoodie", "Hoodie"
        MUG = "mug", "Mug"
        CANVAS = "canvas", "Canvas"
        POSTER = "poster", "Poster"
        PHONE_CASE = "phone_case", "Phone Case"
        TOTE_BAG = "tote_bag", "Tote Bag"

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    product_type = models.CharField(max_length=30, choices=ProductType.choices)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    base_image = models.ImageField(upload_to="mockup-templates/base/", blank=True, null=True)
    mask_image = models.ImageField(upload_to="mockup-templates/masks/", blank=True, null=True)
    displacement_map = models.ImageField(upload_to="mockup-templates/displacement/", blank=True, null=True)
    shadow_layer = models.ImageField(upload_to="mockup-templates/shadows/", blank=True, null=True)
    highlight_layer = models.ImageField(upload_to="mockup-templates/highlights/", blank=True, null=True)
    template_version = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    supported_colors = models.JSONField(default=list, blank=True)
    supported_sizes = models.JSONField(default=list, blank=True)
    canvas_width = models.PositiveIntegerField(
        null=True, blank=True, help_text="Mockup canvas width in pixels, for templates without a root base_image."
    )
    canvas_height = models.PositiveIntegerField(
        null=True, blank=True, help_text="Mockup canvas height in pixels, for templates without a root base_image."
    )
    supported_file_formats = models.JSONField(
        default=list, blank=True, help_text="Accepted print-file formats, e.g. ['png', 'pdf']."
    )
    selected_print_provider = models.ForeignKey(
        "printify.PrintifyPrintProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mockup_templates",
        help_text="Which synced Printify print provider fulfils this template's variants, if mapped.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product_type", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.get_product_type_display()})"


class MockupTemplatePart(models.Model):
    class PartName(models.TextChoices):
        FRONT = "front", "Front"
        BACK = "back", "Back"
        LEFT_SLEEVE = "left_sleeve", "Left Sleeve"
        RIGHT_SLEEVE = "right_sleeve", "Right Sleeve"

    template = models.ForeignKey(MockupTemplate, on_delete=models.CASCADE, related_name="parts")
    name = models.CharField(max_length=30, choices=PartName.choices, default=PartName.FRONT)
    base_image = models.ImageField(upload_to="mockup-templates/parts/base/")
    mask_image = models.ImageField(upload_to="mockup-templates/parts/masks/", blank=True, null=True)
    displacement_map = models.ImageField(upload_to="mockup-templates/parts/displacement/", blank=True, null=True)
    shadow_layer = models.ImageField(upload_to="mockup-templates/parts/shadows/", blank=True, null=True)
    highlight_layer = models.ImageField(upload_to="mockup-templates/parts/highlights/", blank=True, null=True)
    config = models.JSONField(default=dict, blank=True)
    dpi = models.PositiveIntegerField(default=300, help_text="Required print resolution for this print area.")
    safe_area = models.JSONField(
        default=dict,
        blank=True,
        help_text="Safe area as {left, top, width, height} percentages within the print area.",
    )
    bleed_area = models.JSONField(
        default=dict,
        blank=True,
        help_text="Bleed as {top, right, bottom, left} in pixels beyond the print area edges.",
    )
    print_file_width = models.PositiveIntegerField(
        null=True, blank=True, help_text="Required print-file width in pixels at the target DPI."
    )
    print_file_height = models.PositiveIntegerField(
        null=True, blank=True, help_text="Required print-file height in pixels at the target DPI."
    )
    printify_placeholder_position = models.CharField(
        max_length=30,
        blank=True,
        help_text="Printify's placeholder position key for this print area (e.g. 'front', 'sleeve_left') "
        "if it differs from `name` — Printify's naming doesn't always match ours 1:1. Falls back to `name` when blank.",
    )
    printify_placeholder_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw placeholder dimensions/config for this print area from the mapped Printify print provider's variant response.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        unique_together = (("template", "name"),)

    def __str__(self) -> str:
        return f"{self.template.name} - {self.get_name_display()}"


class ProductVariant(models.Model):
    """A specific purchasable colour/size combination, belonging to both a storefront
    Product (what customers browse/buy) and the MockupTemplate that renders it (what the
    customization editor and renderer use). `product` is nullable so a variant can exist
    ahead of the storefront listing being wired up; `template` is required since rendering
    always needs it."""

    product = models.ForeignKey(
        "shop.Product",
        on_delete=models.CASCADE,
        related_name="variants",
        null=True,
        blank=True,
    )
    template = models.ForeignKey(MockupTemplate, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=255, blank=True, help_text="Display name for this variant, e.g. 'Midnight Black / M'.")
    color_name = models.CharField(max_length=120, blank=True)
    color_hex = models.CharField(max_length=7, blank=True, help_text="e.g. #1a1a1a")
    size = models.CharField(max_length=120, blank=True)
    external_provider = models.CharField(max_length=120, blank=True, help_text="Fulfilment provider name, e.g. Printify.")
    external_variant_id = models.CharField(max_length=64, blank=True, help_text="Provider-side variant ID.")
    base_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    inventory = models.PositiveIntegerField(default=0)
    is_available = models.BooleanField(default=True)
    image = models.ImageField(upload_to="product-variants/", blank=True, null=True)
    supported_print_areas = models.JSONField(
        default=list,
        blank=True,
        help_text="Part names this variant supports printing on, e.g. ['front', 'back']. Empty means all template parts.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("template", "color_name", "size")
        constraints = [
            # Two different storefront products may legitimately share one template (e.g. two
            # brands both selling off the same "Starter T-Shirt" template) and each needs to be
            # able to offer "Black / M" independently — so uniqueness for product-linked variants
            # is scoped per-product, not just per-template. Variants with no product yet (created
            # ahead of the storefront listing) fall back to the old template-only uniqueness.
            models.UniqueConstraint(
                fields=["product", "template", "color_name", "size"],
                condition=models.Q(product__isnull=False),
                name="unique_product_template_color_size",
            ),
            models.UniqueConstraint(
                fields=["template", "color_name", "size"],
                condition=models.Q(product__isnull=True),
                name="unique_template_color_size_without_product",
            ),
        ]

    def __str__(self) -> str:
        label = " / ".join(part for part in (self.color_name, self.size) if part)
        return f"{self.template.name} - {label}" if label else self.template.name

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.product_id and self.product.mockup_template_id and self.product.mockup_template_id != self.template_id:
            raise ValidationError(
                "This variant's template must match its product's configured mockup_template."
            )


class DesignProject(models.Model):
    """A customer's saved, reopenable customization of a storefront Product / MockupTemplate.

    Coordinate convention (see also DesignPlacement below): placement x/y/width/height are in
    the same template-pixel space as MockupTemplate/MockupTemplatePart.config['placement']
    (i.e. pixel offsets into the template's base image canvas). Crop left/top/width/height are
    percentages (0-100) of the *source design image*, matching services._sanitize_crop_override.
    Both conventions were already established by MockupRender.placement_override /
    crop_override before this model existed, and are preserved here unchanged.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        ARCHIVED = "archived", "Archived"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="design_projects")
    name = models.CharField(max_length=255, blank=True)
    product = models.ForeignKey(
        "shop.Product",
        on_delete=models.SET_NULL,
        related_name="design_projects",
        null=True,
        blank=True,
    )
    mockup_template = models.ForeignKey(MockupTemplate, on_delete=models.PROTECT, related_name="design_projects")
    selected_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        related_name="design_projects",
        null=True,
        blank=True,
    )
    selected_color = models.CharField(max_length=120, blank=True, help_text="Snapshot of the chosen colour at save time.")
    selected_size = models.CharField(max_length=120, blank=True, help_text="Snapshot of the chosen size at save time.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    source_artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="design_projects",
        null=True,
        blank=True,
        help_text="The primary design this project started from, if any.",
    )
    source_generated_image = models.ForeignKey(
        "generator.GeneratedImage",
        on_delete=models.SET_NULL,
        related_name="design_projects",
        null=True,
        blank=True,
    )
    source_image_url = models.TextField(blank=True)
    source_prompt = models.TextField(blank=True)

    thumbnail = models.ImageField(upload_to="design-projects/thumbnails/", blank=True, null=True)
    thumbnail_url = models.TextField(
        blank=True,
        help_text="URL of an already-rendered preview to use as the thumbnail. Never store base64 data here.",
    )
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=["user", "updated_at"], name="designproj_user_updated_idx"),
            models.Index(fields=["user", "status"], name="designproj_user_status_idx"),
            models.Index(fields=["product"], name="designproj_product_idx"),
            models.Index(fields=["mockup_template"], name="designproj_template_idx"),
        ]

    def __str__(self) -> str:
        return self.name or f"Design Project #{self.pk}"

    def save(self, *args, **kwargs):
        if not self.name:
            product_label = self.mockup_template.get_product_type_display() if self.mockup_template_id else "Design"
            self.name = f"Untitled {product_label} Design"
        super().save(*args, **kwargs)


class DesignPlacement(models.Model):
    """A single print area's design placement within a DesignProject, stored independently
    per part. See DesignProject's docstring for the placement/crop coordinate convention."""

    class Fit(models.TextChoices):
        CONTAIN = "contain", "Contain"
        COVER = "cover", "Cover"

    design_project = models.ForeignKey(DesignProject, on_delete=models.CASCADE, related_name="placements")
    part_name = models.CharField(
        max_length=30,
        choices=MockupTemplatePart.PartName.choices,
        default=MockupTemplatePart.PartName.FRONT,
    )
    template_part = models.ForeignKey(
        MockupTemplatePart,
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
        help_text="The exact template part this placement targets, when the template has parts configured.",
    )

    source_artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
    )
    source_generated_image = models.ForeignKey(
        "generator.GeneratedImage",
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
    )
    source_image_url = models.TextField(blank=True)
    source_prompt = models.TextField(blank=True)

    x = models.FloatField(default=0)
    y = models.FloatField(default=0)
    width = models.FloatField(default=0)
    height = models.FloatField(default=0)
    rotation = models.FloatField(default=0)
    opacity = models.FloatField(default=1)
    corner_radius = models.FloatField(default=0)
    fit = models.CharField(max_length=10, choices=Fit.choices, default=Fit.CONTAIN)

    crop_left = models.FloatField(default=0)
    crop_top = models.FloatField(default=0)
    crop_width = models.FloatField(default=100)
    crop_height = models.FloatField(default=100)

    text_elements = models.JSONField(default=list, blank=True)

    preview_render = models.ForeignKey(
        "generator.MockupRender",
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
    )
    preview_url = models.TextField(blank=True)
    print_file_url = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("design_project", "part_name")
        constraints = [
            models.UniqueConstraint(
                fields=["design_project", "part_name"],
                name="unique_design_project_part",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.design_project} - {self.part_name}"


class MockupRender(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="mockup_renders",
        null=True,
        blank=True,
    )
    generated_image = models.ForeignKey(
        GeneratedImage,
        on_delete=models.CASCADE,
        related_name="mockup_renders",
        null=True,
        blank=True,
    )
    artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="mockup_renders",
        null=True,
        blank=True,
    )
    source_asset = models.ForeignKey(
        SourceDesignAsset,
        on_delete=models.SET_NULL,
        related_name="mockup_renders",
        null=True,
        blank=True,
    )
    source_image_url = models.TextField(blank=True)
    source_prompt = models.TextField(blank=True)
    source_fingerprint = models.CharField(max_length=64)
    template = models.ForeignKey(
        MockupTemplate,
        on_delete=models.CASCADE,
        related_name="renders",
    )
    part_name = models.CharField(max_length=30, blank=True, help_text="Specific part name (e.g. front, back) if rendering a template part.")
    variant_color = models.CharField(max_length=120, blank=True)
    variant_size = models.CharField(max_length=120, blank=True)
    placement_override = models.JSONField(default=dict, blank=True)
    crop_override = models.JSONField(default=dict, blank=True)
    text_elements = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    cache_key = models.CharField(max_length=255, unique=True)
    output_image = models.ImageField(upload_to="mockup-renders/", blank=True, null=True)
    output_image_url = models.TextField(blank=True)
    processing_notes = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    render_started_at = models.DateTimeField(null=True, blank=True)
    render_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.template.name} / {self.status}"
