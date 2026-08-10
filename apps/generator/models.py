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
    class SourceType(models.TextChoices):
        GALLERY = "gallery", "Gallery"
        USER_UPLOAD = "user_upload", "User upload"
        AI_GENERATED = "ai_generated", "AI generated"

    artwork = models.ForeignKey(
        "gallery.Artwork",
        on_delete=models.SET_NULL,
        related_name="source_design_assets",
        null=True,
        blank=True,
    )
    # Null for gallery-derived assets (admin-owned artwork, public by definition — see `artwork`
    # above) and for legacy rows predating this field. Set for a user upload or an AI-generated
    # image saved via the customization editor's action menu — those are private to this owner
    # (see apps.generator.views.SourceDesignAssetUploadView / apps.generator.serializers), never
    # exposed through any public listing endpoint.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="source_design_assets",
        null=True,
        blank=True,
    )
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.GALLERY)
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
    mime_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True, help_text="Original upload size in bytes.")
    has_transparency = models.BooleanField(
        default=False, help_text="Whether the stored image has an alpha channel (structural check, not per-pixel)."
    )
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
    # Draft by default, same as shop.Product — a template isn't usable until it has at least one
    # MockupTemplatePart (enforced in clean() below), so there's nothing meaningful to activate
    # until an admin has added one. There is deliberately no template-level base_image/config/
    # mask_image/displacement_map/shadow_layer/highlight_layer/canvas_width/canvas_height
    # anymore — every renderable surface must be a MockupTemplatePart now, never a "root" image
    # that bypasses Parts entirely (see CHANGELOG.md for the removal).
    is_active = models.BooleanField(default=False)
    template_version = models.PositiveIntegerField(default=1)
    supported_colors = models.JSONField(default=list, blank=True)
    supported_sizes = models.JSONField(default=list, blank=True)
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

    def clean(self):
        super().clean()
        # NOTE: the "must have >= 1 part before activation" rule is deliberately NOT enforced
        # here, even though a template with zero parts renders nothing (see
        # apps.generator.services.render_mockup_to_image, which only ever reads from a
        # MockupTemplatePart). Same reasoning as apps.shop.models.Product / validate_product_
        # can_be_activated: Django Admin saves the parent MockupTemplate row and its inline
        # MockupTemplatePart formset in separate steps (parent first), so clean() running during
        # the parent's own validation would see whatever parts happened to exist *before* this
        # request, not the ones just submitted alongside it in the inline formset — a clean()
        # check here would make "create a template, add its first part inline, check Active, "
        # "save" permanently fail even though it's the normal way to set one up. Enforced instead
        # in apps.generator.serializers.AdminMockupTemplateSerializer.validate() (the custom
        # admin panel never combines part-creation and activation in one request, so no ordering
        # issue there) and in MockupTemplateAdmin.save_model()/save_related() (deferred to after
        # inlines save, mirroring ProductAdmin exactly).
        if self.selected_print_provider_id:
            from django.core.exceptions import ValidationError
            from apps.printify.validation import validate_provider_matches_template_blueprint

            try:
                validate_provider_matches_template_blueprint(self.pk, self.selected_print_provider)
            except ValueError as exc:
                raise ValidationError({"selected_print_provider": str(exc)}) from exc


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

    def clean(self):
        super().clean()
        # Blank is always allowed — it means this part isn't mapped to a Printify placeholder
        # yet, not that it's invalid. Only validate an explicit, non-blank override.
        if self.printify_placeholder_position and self.template_id:
            from django.core.exceptions import ValidationError

            from apps.printify.validation import validate_placeholder_position

            try:
                validate_placeholder_position(self.printify_placeholder_position, self.template.selected_print_provider)
            except ValueError as exc:
                raise ValidationError({"printify_placeholder_position": str(exc)}) from exc


class ProductVariant(models.Model):
    """The single source of truth for sellable pricing, availability and inventory — a specific
    purchasable colour/size combination, belonging to both a storefront `Product` (what
    customers browse/buy) and the `MockupTemplate` that renders it (what the customization
    editor and renderer use). `product` is REQUIRED: every variant must belong to a real
    storefront listing (see CHANGELOG.md for the migration from the old nullable/orphan-capable
    shape). `template` is *also* required and, for now, deliberately still duplicated
    alongside `product.mockup_template` rather than derived from it — see the module-level note
    above `clean()` for why, and TODO.md for tracking its eventual removal."""

    product = models.ForeignKey(
        "shop.Product",
        on_delete=models.CASCADE,
        related_name="variants",
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
    retail_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Admin reference/display only — NOT read by the pricing engine. "
            "apps.cart.pricing.price_item() and apps.shop.services.get_product_starting_price() "
            "both compute from base_cost + markup rules; this field is never used as an override."
        ),
    )
    inventory = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Known physical stock quantity where supplied. Leave empty for print-on-demand "
            "availability — empty inventory with is_available=True means the provider (e.g. "
            "Printify) currently offers this variant, just with no numeric stock count given. "
            "Never a placeholder like 9999 — empty means genuinely unknown, not unlimited."
        ),
    )
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
            # `product` is now required, so this is unconditional — every variant is scoped to
            # its own product's colour/size uniqueness. Two different storefront products may
            # legitimately share one template (e.g. two brands both selling off the same
            # "Starter T-Shirt" template) and each still gets to offer "Black / M" independently.
            models.UniqueConstraint(
                fields=["product", "template", "color_name", "size"],
                name="unique_product_template_color_size",
            ),
            # A given provider-side variant ID should only ever back one local row — guards
            # against a sync bug (or manual data entry) attaching the same Printify variant to
            # two different ProductVariant rows. Blank external_variant_id (a variant with no
            # provider mapping yet) is explicitly exempted, since many rows legitimately share
            # the empty string.
            models.UniqueConstraint(
                fields=["external_provider", "external_variant_id"],
                condition=~models.Q(external_variant_id=""),
                name="unique_external_provider_variant",
            ),
        ]

    def __str__(self) -> str:
        label = " / ".join(part for part in (self.color_name, self.size) if part)
        return f"{self.template.name} - {label}" if label else self.template.name

    def clean(self):
        from django.core.exceptions import ValidationError

        # `ProductVariant.template` duplicates `product.mockup_template` rather than being
        # derived from it — kept deliberately for this task (see TODO.md: removing it is future
        # work, since the renderer/editor currently read placement/part data through `template`
        # directly on a lot of call sites that don't necessarily have `product` loaded). This
        # check is what keeps the two from silently drifting apart in the meantime.
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
    # The uploaded-file or AI-generated `SourceDesignAsset` behind this placement, when the
    # design came from the customization editor's Upload or Generate-with-AI actions (a Gallery
    # selection still uses `source_artwork` above — a SourceDesignAsset is not created for that
    # case). Kept distinct from `source_image_url` (which stays empty for these) so production
    # rendering always reads the original stored file rather than re-deriving from a URL — see
    # `_load_source_image_for_placement` in services.py.
    source_asset = models.ForeignKey(
        "generator.SourceDesignAsset",
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


class GeneratedPrintFile(models.Model):
    """A production print file for one DesignPlacement — transparent background, the customer's
    artwork and visible text only, at the template part's required print-file dimensions/DPI.
    Deliberately a separate model from MockupRender (preview) and never overwrites one: previews
    are a cheap, disposable web approximation; a print file is the thing a paid order would
    actually need preserved. `signature` is a content hash of every printable input (source
    image, placement/crop/rotation/opacity/text, template part production settings) — see
    apps.generator.services.build_print_file_signature() — so an unchanged design reuses its
    existing completed file instead of regenerating, and a changed one gets a fresh row rather
    than mutating history a future paid order might depend on."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    design_placement = models.ForeignKey(
        DesignPlacement, on_delete=models.CASCADE, related_name="generated_print_files"
    )
    template_part = models.ForeignKey(
        MockupTemplatePart, on_delete=models.PROTECT, related_name="generated_print_files"
    )
    output_file = models.ImageField(upload_to="design-projects/print-files/", blank=True, null=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    dpi = models.PositiveIntegerField(default=0)
    signature = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="sha256 of every printable input — see build_print_file_signature(). Used to "
        "detect staleness and reuse an existing completed file instead of regenerating.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["design_placement", "signature"]),
        ]

    def __str__(self) -> str:
        return f"{self.design_placement} print file ({self.status})"
