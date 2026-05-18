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
    base_image = models.ImageField(upload_to="mockup-templates/base/")
    mask_image = models.ImageField(upload_to="mockup-templates/masks/", blank=True, null=True)
    shadow_layer = models.ImageField(upload_to="mockup-templates/shadows/", blank=True, null=True)
    highlight_layer = models.ImageField(upload_to="mockup-templates/highlights/", blank=True, null=True)
    template_version = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    supported_colors = models.JSONField(default=list, blank=True)
    supported_sizes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product_type", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.get_product_type_display()})"


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
    variant_color = models.CharField(max_length=120, blank=True)
    variant_size = models.CharField(max_length=120, blank=True)
    placement_override = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    cache_key = models.CharField(max_length=255, unique=True)
    output_image = models.ImageField(upload_to="mockup-renders/", blank=True, null=True)
    output_image_url = models.URLField(blank=True)
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
