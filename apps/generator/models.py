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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        unique_together = (("template", "name"),)

    def __str__(self) -> str:
        return f"{self.template.name} - {self.get_name_display()}"


class ProductVariant(models.Model):
    """A specific purchasable colour/size combination of a MockupTemplate."""

    template = models.ForeignKey(MockupTemplate, on_delete=models.CASCADE, related_name="variants")
    colour = models.CharField(max_length=120, blank=True)
    size = models.CharField(max_length=120, blank=True)
    print_provider = models.CharField(max_length=120, blank=True)
    base_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_available = models.BooleanField(default=True)
    printify_variant_id = models.CharField(max_length=64, blank=True)
    image = models.ImageField(upload_to="product-variants/", blank=True, null=True)
    supported_print_areas = models.JSONField(
        default=list,
        blank=True,
        help_text="Part names this variant supports printing on, e.g. ['front', 'back']. Empty means all template parts.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("template", "colour", "size")
        unique_together = (("template", "colour", "size"),)

    def __str__(self) -> str:
        label = " / ".join(part for part in (self.colour, self.size) if part)
        return f"{self.template.name} - {label}" if label else self.template.name


class DesignProject(models.Model):
    """A customer's saved, reopenable customization of a MockupTemplate."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SAVED = "saved", "Saved"
        ORDERED = "ordered", "Ordered"
        ARCHIVED = "archived", "Archived"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="design_projects")
    name = models.CharField(max_length=255, blank=True)
    template = models.ForeignKey(MockupTemplate, on_delete=models.PROTECT, related_name="design_projects")
    selected_colour = models.CharField(max_length=120, blank=True)
    selected_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        related_name="design_projects",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    thumbnail = models.ImageField(upload_to="design-projects/thumbnails/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.name or f"Design Project #{self.pk}"


class DesignPlacement(models.Model):
    """A single print area's design placement within a DesignProject, stored independently per part."""

    design_project = models.ForeignKey(DesignProject, on_delete=models.CASCADE, related_name="placements")
    product_part = models.CharField(
        max_length=30,
        choices=MockupTemplatePart.PartName.choices,
        default=MockupTemplatePart.PartName.FRONT,
    )
    artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
    )
    generated_image = models.ForeignKey(
        "generator.GeneratedImage",
        on_delete=models.SET_NULL,
        related_name="design_placements",
        null=True,
        blank=True,
    )
    x_position = models.FloatField(default=0)
    y_position = models.FloatField(default=0)
    width = models.FloatField(default=0)
    height = models.FloatField(default=0)
    rotation = models.FloatField(default=0)
    opacity = models.FloatField(default=1)
    crop_data = models.JSONField(default=dict, blank=True)
    corner_radius = models.FloatField(default=0)
    text_settings = models.JSONField(default=list, blank=True)
    preview_url = models.TextField(blank=True)
    print_file_url = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("design_project", "product_part")
        unique_together = (("design_project", "product_part"),)

    def __str__(self) -> str:
        return f"{self.design_project} - {self.product_part}"


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
