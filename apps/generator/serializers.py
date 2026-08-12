from rest_framework import serializers

from apps.shop.models import Product

from .models import (
    DesignPlacement,
    DesignProject,
    GeneratedImage,
    GeneratedPrintFile,
    GenerationRequest,
    MockupRender,
    MockupTemplate,
    MockupTemplatePart,
    MockupTemplatePartColorAsset,
    ProductVariant,
    SourceDesignAsset,
)


class GenerationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationRequest
        fields = (
            "id",
            "prompt",
            "style",
            "provider",
            "model_name",
            "status",
            "error_message",
            "created_at",
        )
        read_only_fields = ("id", "status", "error_message", "created_at")


class GeneratedImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedImage
        fields = ("id", "prompt", "image", "image_url", "created_at")

    def get_image(self, obj: GeneratedImage):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None


class SourceDesignAssetSerializer(serializers.ModelSerializer):
    """Response shape for SourceDesignAssetUploadView. No separate thumbnail field on the model
    — an upload/AI-generated result is already a reasonably-sized single design asset, not a
    huge original needing a distinct thumbnail the way gallery Artwork/shop Product do — so
    thumbnail_url is just an alias for file_url."""

    file_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = SourceDesignAsset
        fields = (
            "id",
            "source_type",
            "file_url",
            "thumbnail_url",
            "width",
            "height",
            "has_transparency",
            "created_at",
        )

    def _get_file_url(self, obj: SourceDesignAsset):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None

    def get_file_url(self, obj: SourceDesignAsset):
        return self._get_file_url(obj)

    def get_thumbnail_url(self, obj: SourceDesignAsset):
        return self._get_file_url(obj)


class MockupTemplatePartColorAssetSerializer(serializers.ModelSerializer):
    base_image = serializers.SerializerMethodField()
    mask_image = serializers.SerializerMethodField()
    displacement_map = serializers.SerializerMethodField()
    shadow_layer = serializers.SerializerMethodField()
    highlight_layer = serializers.SerializerMethodField()

    class Meta:
        model = MockupTemplatePartColorAsset
        fields = (
            "id",
            "color_name",
            "base_image",
            "mask_image",
            "displacement_map",
            "shadow_layer",
            "highlight_layer",
        )

    def _get_file_url(self, file_field):
        if not file_field:
            return None
        try:
            return file_field.url
        except Exception:
            return None

    def get_base_image(self, obj: MockupTemplatePartColorAsset):
        return self._get_file_url(obj.base_image)

    def get_mask_image(self, obj: MockupTemplatePartColorAsset):
        return self._get_file_url(obj.mask_image)

    def get_displacement_map(self, obj: MockupTemplatePartColorAsset):
        return self._get_file_url(obj.displacement_map)

    def get_shadow_layer(self, obj: MockupTemplatePartColorAsset):
        return self._get_file_url(obj.shadow_layer)

    def get_highlight_layer(self, obj: MockupTemplatePartColorAsset):
        return self._get_file_url(obj.highlight_layer)


class MockupTemplatePartSerializer(serializers.ModelSerializer):
    base_image = serializers.SerializerMethodField()
    mask_image = serializers.SerializerMethodField()
    displacement_map = serializers.SerializerMethodField()
    shadow_layer = serializers.SerializerMethodField()
    highlight_layer = serializers.SerializerMethodField()
    color_assets = MockupTemplatePartColorAssetSerializer(many=True, read_only=True)

    class Meta:
        model = MockupTemplatePart
        fields = (
            "id",
            "name",
            "base_image",
            "mask_image",
            "displacement_map",
            "shadow_layer",
            "highlight_layer",
            "color_assets",
            "config",
            "dpi",
            "safe_area",
            "bleed_area",
            "print_file_width",
            "print_file_height",
        )

    def _get_file_url(self, file_field):
        if not file_field:
            return None
        try:
            return file_field.url
        except Exception:
            return None

    def get_base_image(self, obj: MockupTemplatePart):
        return self._get_file_url(obj.base_image)

    def get_mask_image(self, obj: MockupTemplatePart):
        return self._get_file_url(obj.mask_image)

    def get_displacement_map(self, obj: MockupTemplatePart):
        return self._get_file_url(obj.displacement_map)

    def get_shadow_layer(self, obj: MockupTemplatePart):
        return self._get_file_url(obj.shadow_layer)

    def get_highlight_layer(self, obj: MockupTemplatePart):
        return self._get_file_url(obj.highlight_layer)


class ProductVariantSerializer(serializers.ModelSerializer):
    """`product_id` is always numeric — `ProductVariant.product` is a required FK (see
    apps.shop CHANGELOG entry on the product/variant refactor), never null.

    `is_available` vs. `is_sellable` vs. `pricing_ready` are three deliberately different
    signals, not synonyms:
    - `is_available` — provider/catalogue availability (does Printify/the admin currently offer
      this colour+size at all).
    - `pricing_ready` — specifically whether a production cost is configured
      (`base_cost is not None`); a cheap, narrow check the frontend can use to show a "pricing
      not configured" reason distinct from "unavailable."
    - `is_sellable` — the full commercial-readiness check (available AND priced AND matches the
      product's template AND has a valid provider mapping where one is claimed) — the single
      source of truth is `apps.shop.services.variant_is_sellable`, reused as-is here rather than
      reimplemented; product-level `starting_price`/`is_available`/`available_variant_count`
      (apps.shop.serializers.ProductSerializer) are derived from the exact same function, so the
      two can never disagree."""

    image = serializers.SerializerMethodField()
    product_id = serializers.SerializerMethodField()
    template_id = serializers.IntegerField(read_only=True)
    price = serializers.DecimalField(source="retail_price", max_digits=10, decimal_places=2, read_only=True)
    is_sellable = serializers.SerializerMethodField()
    pricing_ready = serializers.SerializerMethodField()

    def get_product_id(self, obj: ProductVariant):
        return obj.product_id

    def get_pricing_ready(self, obj: ProductVariant):
        return obj.base_cost is not None

    def get_is_sellable(self, obj: ProductVariant):
        # Deferred import: apps.shop.services doesn't import apps.generator at module load time
        # (this app already imports apps.shop.models at its own top — see apps/shop/services.py's
        # module docstring), so importing it here rather than at this file's top avoids a cycle.
        from apps.shop.services import variant_is_sellable

        # `mockup_template_id` in context lets a caller that already has the parent Product
        # loaded (apps.shop.serializers.ProductSerializer.get_variants) avoid an extra query per
        # variant; omitted, variant_is_sellable() falls back to `obj.product.mockup_template_id`
        # (a query unless the queryset already select_related("product") — see
        # apps.generator.views.ProductVariantListView).
        mockup_template_id = self.context.get("mockup_template_id")
        return variant_is_sellable(obj, mockup_template_id=mockup_template_id)

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "product_id",
            "template_id",
            "sku",
            "name",
            "color_name",
            "color_hex",
            "size",
            "price",
            "base_cost",
            "inventory",
            "is_available",
            "is_sellable",
            "pricing_ready",
            "supported_print_areas",
            "external_provider",
            "external_variant_id",
            "image",
            "updated_at",
        )

    def get_image(self, obj: ProductVariant):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None


class MockupTemplateSerializer(serializers.ModelSerializer):
    """Note: deliberately does NOT embed `variants` — that used to return every variant for
    every template regardless of which storefront product asked, which balloons payload size
    for templates shared across many products. Fetch variants for a specific product/template
    via GET /api/generator/product-variants/?product_id=&template_id= instead (see
    ProductVariantListView), or shop.ProductSerializer.variants for a specific product.

    No template-level base_image/mask_image/displacement_map/shadow_layer/highlight_layer/
    config/canvas_width/canvas_height fields anymore — every renderable surface is a
    MockupTemplatePart now (`parts` below), never a template-level "root" image. A template
    with zero parts can't be `is_active` (see MockupTemplate.clean()), so `parts` is never
    empty for anything this serializer would realistically be asked to represent."""

    product_type_display = serializers.CharField(source="get_product_type_display", read_only=True)
    parts = MockupTemplatePartSerializer(many=True, read_only=True)

    class Meta:
        model = MockupTemplate
        fields = (
            "id",
            "name",
            "slug",
            "product_type",
            "product_type_display",
            "description",
            "is_active",
            "template_version",
            "supported_colors",
            "supported_sizes",
            "supported_file_formats",
            "parts",
            "updated_at",
        )


class MockupRenderSerializer(serializers.ModelSerializer):
    template = MockupTemplateSerializer(read_only=True)
    output_image = serializers.SerializerMethodField()

    class Meta:
        model = MockupRender
        fields = (
            "id",
            "template",
            "generated_image",
            "artwork",
            "source_asset",
            "source_image_url",
            "source_prompt",
            "part_name",
            "variant_color",
            "variant_size",
            "placement_override",
            "crop_override",
            "text_elements",
            "status",
            "cache_key",
            "output_image",
            "output_image_url",
            "processing_notes",
            "error_message",
            "render_started_at",
            "render_completed_at",
            "created_at",
            "updated_at",
        )

    def get_output_image(self, obj: MockupRender):
        if not obj.output_image:
            return None
        try:
            return obj.output_image.url
        except Exception:
            return None


class MockupRenderCreateSerializer(serializers.Serializer):
    generated_image_id = serializers.IntegerField(required=False)
    artwork_id = serializers.IntegerField(required=False)
    source_image_url = serializers.CharField(required=False, allow_blank=False)
    source_prompt = serializers.CharField(required=False, allow_blank=True)
    template_id = serializers.IntegerField()
    part_name = serializers.CharField(required=False, allow_blank=True, max_length=30)
    variant_color = serializers.CharField(required=False, allow_blank=True, max_length=120)
    variant_size = serializers.CharField(required=False, allow_blank=True, max_length=120)
    placement_override = serializers.DictField(required=False, allow_null=True)
    crop_override = serializers.DictField(required=False, allow_null=True)
    text_elements = serializers.ListField(
        child=serializers.DictField(), required=False, allow_null=True
    )

    def validate(self, attrs):
        generated_image_id = attrs.get("generated_image_id")
        artwork_id = attrs.get("artwork_id")
        source_image_url = attrs.get("source_image_url")
        if not generated_image_id and not artwork_id and not source_image_url:
            raise serializers.ValidationError(
                "Provide generated_image_id, artwork_id, or source_image_url for mockup rendering."
            )

        placement_override = attrs.get("placement_override")
        if isinstance(placement_override, dict):
            normalized_override = {}
            for key in ("x", "y", "width", "height", "rotation", "opacity", "corner_radius"):
                value = placement_override.get(key)
                if value is None or value == "":
                    continue
                if key == "opacity":
                    normalized_override[key] = float(value)
                else:
                    normalized_override[key] = int(float(value))

            fit = placement_override.get("fit")
            if isinstance(fit, str) and fit.strip():
                normalized_override["fit"] = fit.strip().lower()

            attrs["placement_override"] = normalized_override

        crop_override = attrs.get("crop_override")
        if isinstance(crop_override, dict):
            normalized_crop = {}
            for key in ("left", "top", "width", "height"):
                value = crop_override.get(key)
                if value is None or value == "":
                    continue
                normalized_crop[key] = float(value)

            attrs["crop_override"] = normalized_crop

        return attrs


class ProductSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "slug", "mockup_template")


class PlacementOverrideSerializer(serializers.Serializer):
    """Maps to/from DesignPlacement.x/y/width/height/rotation/opacity/corner_radius/fit.
    Coordinates are template-pixel space, matching MockupTemplate(Part).config['placement']."""

    x = serializers.FloatField(required=False, default=0)
    y = serializers.FloatField(required=False, default=0)
    width = serializers.FloatField(required=False, default=0, min_value=0)
    height = serializers.FloatField(required=False, default=0, min_value=0)
    rotation = serializers.FloatField(required=False, default=0)
    opacity = serializers.FloatField(required=False, default=1, min_value=0, max_value=1)
    corner_radius = serializers.FloatField(required=False, default=0, min_value=0)
    fit = serializers.ChoiceField(choices=DesignPlacement.Fit.choices, required=False, default=DesignPlacement.Fit.CONTAIN)


class CropOverrideSerializer(serializers.Serializer):
    """Maps to/from DesignPlacement.crop_left/crop_top/crop_width/crop_height. Values are
    percentages (0-100) of the source design image, matching services._sanitize_crop_override."""

    left = serializers.FloatField(required=False, default=0)
    top = serializers.FloatField(required=False, default=0)
    width = serializers.FloatField(required=False, default=100, min_value=0.01)
    height = serializers.FloatField(required=False, default=100, min_value=0.01)


class DesignPlacementSerializer(serializers.ModelSerializer):
    """Read serializer. Composes placement_override/crop_override to match the same
    nested shape the frontend already sends to /mockup-renders/, instead of exposing the
    8 underlying flat columns individually."""

    placement_override = serializers.SerializerMethodField()
    crop_override = serializers.SerializerMethodField()
    preview_render_id = serializers.IntegerField(read_only=True)
    source_artwork_id = serializers.IntegerField(read_only=True)
    source_generated_image_id = serializers.IntegerField(read_only=True)
    source_asset_id = serializers.IntegerField(read_only=True)
    template_part_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = DesignPlacement
        fields = (
            "id",
            "part_name",
            "template_part_id",
            "source_artwork_id",
            "source_generated_image_id",
            "source_asset_id",
            "source_image_url",
            "source_prompt",
            "placement_override",
            "crop_override",
            "text_elements",
            "preview_render_id",
            "preview_url",
            "print_file_url",
            "metadata",
            "created_at",
            "updated_at",
        )

    def get_placement_override(self, obj: DesignPlacement):
        return {
            "x": obj.x,
            "y": obj.y,
            "width": obj.width,
            "height": obj.height,
            "rotation": obj.rotation,
            "opacity": obj.opacity,
            "corner_radius": obj.corner_radius,
            "fit": obj.fit,
        }

    def get_crop_override(self, obj: DesignPlacement):
        return {
            "left": obj.crop_left,
            "top": obj.crop_top,
            "width": obj.crop_width,
            "height": obj.crop_height,
        }


class DesignPlacementWriteSerializer(serializers.Serializer):
    """Per-item validation for a placement inside a design-project write payload. Cross-field
    validation that needs the parent project's template/variant (part-vs-template consistency,
    generated-image ownership) happens in DesignProjectWriteSerializer.validate(), which has
    the request context and the other fields needed to check against."""

    part_name = serializers.ChoiceField(choices=MockupTemplatePart.PartName.choices)
    source_artwork_id = serializers.IntegerField(required=False, allow_null=True)
    source_generated_image_id = serializers.IntegerField(required=False, allow_null=True)
    # An uploaded file or a stored AI-generated result (see SourceDesignAssetUploadView) — never
    # set for a Gallery selection, which still uses source_artwork_id above. Ownership (must be
    # owned by the requesting user) is validated in DesignProjectWriteSerializer.validate(),
    # alongside the existing source_generated_image_id ownership check.
    source_asset_id = serializers.IntegerField(required=False, allow_null=True)
    source_image_url = serializers.CharField(required=False, allow_blank=True, default="")
    source_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    placement_override = PlacementOverrideSerializer(required=False)
    crop_override = CropOverrideSerializer(required=False)
    text_elements = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    preview_render_id = serializers.IntegerField(required=False, allow_null=True)
    preview_url = serializers.CharField(required=False, allow_blank=True, default="")
    print_file_url = serializers.CharField(required=False, allow_blank=True, default="")
    metadata = serializers.DictField(required=False, default=dict)


def _placement_write_data_to_model_fields(item: dict) -> dict:
    """Flattens a validated DesignPlacementWriteSerializer item into DesignPlacement model
    field kwargs (excluding design_project/template_part, which the caller resolves)."""

    placement = item.get("placement_override") or {}
    crop = item.get("crop_override") or {}
    return {
        "part_name": item["part_name"],
        "source_artwork_id": item.get("source_artwork_id"),
        "source_generated_image_id": item.get("source_generated_image_id"),
        "source_asset_id": item.get("source_asset_id"),
        "source_image_url": item.get("source_image_url", ""),
        "source_prompt": item.get("source_prompt", ""),
        "x": placement.get("x", 0),
        "y": placement.get("y", 0),
        "width": placement.get("width", 0),
        "height": placement.get("height", 0),
        "rotation": placement.get("rotation", 0),
        "opacity": placement.get("opacity", 1),
        "corner_radius": placement.get("corner_radius", 0),
        "fit": placement.get("fit", DesignPlacement.Fit.CONTAIN),
        "crop_left": crop.get("left", 0),
        "crop_top": crop.get("top", 0),
        "crop_width": crop.get("width", 100),
        "crop_height": crop.get("height", 100),
        "text_elements": item.get("text_elements", []),
        "preview_render_id": item.get("preview_render_id"),
        "preview_url": item.get("preview_url", ""),
        "print_file_url": item.get("print_file_url", ""),
        "metadata": item.get("metadata", {}),
    }


def resolve_display_thumbnail_url(project: "DesignProject") -> str:
    """The one authoritative thumbnail for a design project, in fallback order:
    1. uploaded project thumbnail, 2. project.thumbnail_url, 3. the 'front' placement's
    preview_url, 4. the first placement (in any order) with a preview_url, 5. the mockup
    template's 'front' part's base image (or its first part, if it has no 'front'),
    6. empty string (frontend shows a neutral placeholder).

    Expects `project.placements.all()` to already be prefetched by the caller — this walks
    the prefetched list in Python rather than issuing new queries.
    """
    if project.thumbnail:
        try:
            return project.thumbnail.url
        except Exception:
            pass

    if project.thumbnail_url:
        return project.thumbnail_url

    placements = list(project.placements.all())

    front = next((p for p in placements if p.part_name == "front" and p.preview_url), None)
    if front:
        return front.preview_url

    first_with_preview = next((p for p in placements if p.preview_url), None)
    if first_with_preview:
        return first_with_preview.preview_url

    if project.mockup_template_id:
        parts = list(project.mockup_template.parts.all())
        front_part = next((p for p in parts if p.name == "front" and p.base_image), None)
        part = front_part or next((p for p in parts if p.base_image), None)
        if part:
            try:
                return part.base_image.url
            except Exception:
                pass

    return ""


class DesignProjectListSerializer(serializers.ModelSerializer):
    """Lightweight summary for the list endpoint — no nested placements."""

    product = ProductSummarySerializer(read_only=True)
    template = serializers.SerializerMethodField()
    selected_variant = ProductVariantSerializer(read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    display_thumbnail_url = serializers.SerializerMethodField()
    placement_count = serializers.SerializerMethodField()

    class Meta:
        model = DesignProject
        fields = (
            "id",
            "name",
            "status",
            "product",
            "template",
            "selected_variant",
            "selected_color",
            "selected_size",
            "thumbnail_url",
            "display_thumbnail_url",
            "placement_count",
            "created_at",
            "updated_at",
        )

    def get_template(self, obj: DesignProject):
        return {
            "id": obj.mockup_template_id,
            "name": obj.mockup_template.name,
            "product_type": obj.mockup_template.product_type,
        }

    def get_placement_count(self, obj: DesignProject):
        # The list view annotates this to avoid a per-row COUNT query; fall back to a live
        # count if the serializer is used somewhere that didn't apply that annotation.
        annotated = getattr(obj, "placement_count_annotated", None)
        return annotated if annotated is not None else obj.placements.count()

    def get_thumbnail_url(self, obj: DesignProject):
        if obj.thumbnail:
            try:
                return obj.thumbnail.url
            except Exception:
                pass
        return obj.thumbnail_url or None

    def get_display_thumbnail_url(self, obj: DesignProject):
        return resolve_display_thumbnail_url(obj)


class DesignProjectSerializer(serializers.ModelSerializer):
    """Full detail — everything required to rebuild the editor."""

    placements = DesignPlacementSerializer(many=True, read_only=True)
    product = ProductSummarySerializer(read_only=True)
    mockup_template = MockupTemplateSerializer(read_only=True)
    selected_variant = ProductVariantSerializer(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    source_artwork_id = serializers.IntegerField(read_only=True)
    source_generated_image_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = DesignProject
        fields = (
            "id",
            "name",
            "product",
            "mockup_template",
            "selected_variant",
            "selected_color",
            "selected_size",
            "status",
            "source_artwork_id",
            "source_generated_image_id",
            "source_image_url",
            "source_prompt",
            "thumbnail",
            "thumbnail_url",
            "metadata",
            "placements",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("status", "created_at", "updated_at")

    def get_thumbnail(self, obj: DesignProject):
        if not obj.thumbnail:
            return None
        try:
            return obj.thumbnail.url
        except Exception:
            return None


class DesignProjectWriteSerializer(serializers.Serializer):
    """Plain Serializer (not ModelSerializer) so create/update/nested-placement semantics can
    be controlled explicitly by the view (transactions, PATCH-vs-PUT), matching the existing
    MockupRenderCreateSerializer pattern in this file rather than fighting DRF's automatic
    nested-write machinery."""

    # No `default=` on any of these: for a partial (PATCH) update we need to tell whether a
    # key was actually present in the request vs merely absent, so validate() can fall back to
    # the existing instance's value for anything the client didn't send. `default=` would make
    # DRF populate the key unconditionally and defeat that. Create-time fallbacks (e.g. "" for
    # an omitted optional text field) are applied explicitly in the view instead.
    name = serializers.CharField(required=False, allow_blank=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    mockup_template_id = serializers.IntegerField(required=False)
    selected_variant_id = serializers.IntegerField(required=False, allow_null=True)
    selected_color = serializers.CharField(required=False, allow_blank=True)
    selected_size = serializers.CharField(required=False, allow_blank=True)
    source_artwork_id = serializers.IntegerField(required=False, allow_null=True)
    source_generated_image_id = serializers.IntegerField(required=False, allow_null=True)
    source_image_url = serializers.CharField(required=False, allow_blank=True)
    source_prompt = serializers.CharField(required=False, allow_blank=True)
    thumbnail_url = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)
    placements = DesignPlacementWriteSerializer(many=True, required=False)

    def validate_thumbnail_url(self, value):
        if value.strip().startswith("data:"):
            raise serializers.ValidationError("thumbnail_url must be a URL, not inline base64 data.")
        return value

    def validate_placements(self, value):
        seen_parts = set()
        for placement in value:
            part = placement.get("part_name")
            if part in seen_parts:
                raise serializers.ValidationError(f"Duplicate placement for part_name '{part}'.")
            seen_parts.add(part)
        return value

    def validate(self, attrs):
        request = self.context["request"]
        instance: DesignProject | None = self.instance
        errors = {}

        # --- mockup_template: required on create; falls back to the existing value on update ---
        if "mockup_template_id" in attrs:
            template = MockupTemplate.objects.filter(pk=attrs["mockup_template_id"], is_active=True).first()
            if not template:
                errors["mockup_template_id"] = "No active mockup template with this id."
        elif instance is not None:
            template = instance.mockup_template
        else:
            template = None
            errors["mockup_template_id"] = "This field is required."
        attrs["_template"] = template

        # --- product: optional; falls back to the existing value on update ---
        if "product_id" in attrs:
            product_id = attrs["product_id"]
            if product_id is None:
                product = None
            else:
                product = Product.objects.filter(pk=product_id, is_active=True).select_related("mockup_template").first()
                if not product:
                    errors["product_id"] = "No active product with this id."
                elif template and product.mockup_template_id and product.mockup_template_id != template.id:
                    errors["product_id"] = "This product is not linked to the selected mockup_template."
        elif instance is not None:
            product = instance.product
        else:
            product = None
        attrs["_product"] = product

        # --- variant: optional; falls back to the existing value on update ---
        if "selected_variant_id" in attrs:
            variant_id = attrs["selected_variant_id"]
            if variant_id is None:
                variant = None
            else:
                variant = ProductVariant.objects.filter(pk=variant_id).select_related("product").first()
                if not variant:
                    errors["selected_variant_id"] = "No product variant with this id."
                else:
                    if template and variant.template_id != template.id:
                        errors["selected_variant_id"] = "This variant does not belong to the selected mockup_template."
                    # variant.product_id is always set now (ProductVariant.product is a required
                    # FK) — no need to guard against a null product_id here anymore.
                    if product and variant.product_id != product.id:
                        errors["selected_variant_id"] = "This variant does not belong to the selected product."
                    if not variant.is_available:
                        errors["selected_variant_id"] = "This variant is not available."
                    # Commercial sellability (configured production cost, valid provider mapping)
                    # on top of the relationship/availability checks above — only evaluated for a
                    # product-backed selection with a known template, and only when none of the
                    # more specific checks above already failed, so a template/product mismatch
                    # or unavailable-variant error is never masked by this more generic message.
                    # Scoped to THIS request's freshly-resolved variant (not the `elif instance is
                    # not None` branch below) so a write that doesn't touch selected_variant_id at
                    # all never re-validates an existing project's already-set variant — a saved
                    # design whose variant later becomes unsellable stays fully readable and
                    # editable for everything else. Deferred import: apps.shop.services doesn't
                    # import apps.generator at module load time (this app already imports
                    # apps.shop.models at its own top — see apps/shop/services.py's module
                    # docstring), so importing it here rather than at this file's top avoids a
                    # cycle.
                    if (
                        variant is not None
                        and product is not None
                        and template is not None
                        and "selected_variant_id" not in errors
                    ):
                        from apps.shop.services import variant_is_sellable

                        if not variant_is_sellable(
                            variant,
                            mockup_template_id=template.id,
                        ):
                            errors["selected_variant_id"] = (
                                "This variant is not currently sellable."
                            )
        elif instance is not None:
            variant = instance.selected_variant
        else:
            variant = None
        attrs["_variant"] = variant

        if variant:
            attrs.setdefault("selected_color", variant.color_name)
            attrs.setdefault("selected_size", variant.size)

        if "source_generated_image_id" in attrs and attrs["source_generated_image_id"] is not None:
            owned = GeneratedImage.objects.filter(pk=attrs["source_generated_image_id"], user=request.user).exists()
            if not owned:
                errors["source_generated_image_id"] = "This generated image does not belong to you."

        # Every active template has >= 1 part (MockupTemplate.clean()) — there's no more "this
        # template has no configured parts, only 'front' is valid" fallback branch to consider.
        # template_parts_by_name is only ever {} here when `template` itself is None, i.e. the
        # mockup_template_id validation above already failed and added its own error.
        template_parts_by_name = {part.name: part for part in template.parts.all()} if template else {}

        placement_errors = []
        for placement in attrs.get("placements", []):
            item_errors = {}
            part_name = placement["part_name"]

            matched_part = template_parts_by_name.get(part_name)
            if template is not None and not matched_part:
                item_errors["part_name"] = f"Template '{template.slug}' has no part named '{part_name}'."
            placement["_template_part"] = matched_part

            if variant and variant.supported_print_areas and part_name not in variant.supported_print_areas:
                item_errors["part_name"] = f"The selected variant does not support printing on '{part_name}'."

            image_id = placement.get("source_generated_image_id")
            if image_id is not None:
                owned = GeneratedImage.objects.filter(pk=image_id, user=request.user).exists()
                if not owned:
                    item_errors["source_generated_image_id"] = "This generated image does not belong to you."

            source_asset_id = placement.get("source_asset_id")
            if source_asset_id is not None:
                # A gallery-derived SourceDesignAsset (owner is null — see the model docstring)
                # is never referenced here (Gallery selections use source_artwork_id instead), so
                # this is always an ownership check, never a "does this exist" check against a
                # legitimately-ownerless row.
                owned = SourceDesignAsset.objects.filter(pk=source_asset_id, owner=request.user).exists()
                if not owned:
                    item_errors["source_asset_id"] = "This design asset does not belong to you."

            preview_render_id = placement.get("preview_render_id")
            if preview_render_id is not None:
                if not MockupRender.objects.filter(pk=preview_render_id).exists():
                    item_errors["preview_render_id"] = "No mockup render with this id."

            placement_errors.append(item_errors)

        if any(placement_errors):
            errors["placements"] = placement_errors

        if errors:
            raise serializers.ValidationError(errors)

        return attrs


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminMockupTemplatePartSerializer(serializers.ModelSerializer):
    """Unlike the public MockupTemplatePartSerializer above, image fields are plain writable
    ImageFields here (not SerializerMethodField URLs) — this is a create/edit form, not a
    read-only render payload. `config`/`safe_area`/`bleed_area` are edited as raw JSON per the
    plan's disclosed simplification — Django admin's visual drag-resize widget is not rebuilt
    here; both edit the same underlying JSON either way.

    `base_image` can also be set from one of the mapped Printify blueprint's own synced catalogue
    images instead of an uploaded file — `printify_blueprint_id`/`printify_image_index` (write-
    only, not model fields) name that image by *position* in `PrintifyBlueprint.images`, never by
    a raw URL the client supplies directly: the actual URL is always resolved server-side from
    already-synced, already-trusted data, so there's no SSRF surface from arbitrary client input
    here the way there would be if this accepted an arbitrary image URL."""

    printify_blueprint_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    printify_image_index = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = MockupTemplatePart
        fields = (
            "id",
            "template",
            "name",
            "base_image",
            "printify_blueprint_id",
            "printify_image_index",
            "mask_image",
            "displacement_map",
            "shadow_layer",
            "highlight_layer",
            "config",
            "dpi",
            "safe_area",
            "bleed_area",
            "print_file_width",
            "print_file_height",
            "printify_placeholder_position",
            "printify_placeholder_config",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {"base_image": {"required": False}}

    def validate(self, attrs):
        has_uploaded_file = attrs.get("base_image") is not None
        has_printify_selection = attrs.get("printify_blueprint_id") is not None and attrs.get("printify_image_index") is not None
        has_existing_image = bool(self.instance and self.instance.base_image)
        if not has_uploaded_file and not has_printify_selection and not has_existing_image:
            raise serializers.ValidationError(
                {"base_image": "Upload a file or select a Printify catalog image."}
            )
        return attrs

    def _resolve_printify_image_url(self, validated_data: dict) -> str | None:
        blueprint_id = validated_data.pop("printify_blueprint_id", None)
        image_index = validated_data.pop("printify_image_index", None)
        if blueprint_id is None or image_index is None:
            return None

        from apps.printify.models import PrintifyBlueprint

        try:
            blueprint = PrintifyBlueprint.objects.get(pk=blueprint_id)
        except PrintifyBlueprint.DoesNotExist:
            raise serializers.ValidationError({"printify_blueprint_id": "No such Printify blueprint."})

        images = blueprint.images or []
        if not isinstance(image_index, int) or not (0 <= image_index < len(images)):
            raise serializers.ValidationError({"printify_image_index": "Invalid image index for this blueprint."})

        url = images[image_index]
        if not isinstance(url, str) or not url.startswith("https://"):
            raise serializers.ValidationError({"printify_image_index": "This catalogue image entry isn't a usable URL."})
        return url

    def _apply_printify_image(self, instance: MockupTemplatePart, image_url: str) -> None:
        from .services import UploadValidationError, fetch_base_image_from_printify_url

        try:
            filename, content = fetch_base_image_from_printify_url(image_url)
        except UploadValidationError as exc:
            raise serializers.ValidationError({"printify_image_index": str(exc)})
        instance.base_image.save(filename, content, save=False)

    def create(self, validated_data):
        image_url = self._resolve_printify_image_url(validated_data)
        instance = MockupTemplatePart(**validated_data)
        if image_url:
            self._apply_printify_image(instance, image_url)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        image_url = self._resolve_printify_image_url(validated_data)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if image_url:
            self._apply_printify_image(instance, image_url)
        instance.save()
        return instance


class AdminMockupTemplatePartColorAssetSerializer(serializers.ModelSerializer):
    """Writable admin form for a single (part, colour) override. `color_name` should match the
    `ProductVariant.color_name` values an admin expects customers to select — matching against
    it at render/preview time is case-insensitive (see apps.generator.services.
    resolve_part_color_asset), so exact casing here doesn't matter.

    `base_image` can also be set from one of a Printify blueprint's own synced catalogue images,
    the same as AdminMockupTemplatePartSerializer above — `printify_blueprint_id`/
    `printify_image_index` (write-only, not model fields) name that image by *position*, never a
    raw client-supplied URL, so there's no SSRF surface here either."""

    printify_blueprint_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    printify_image_index = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = MockupTemplatePartColorAsset
        fields = (
            "id",
            "part",
            "color_name",
            "base_image",
            "printify_blueprint_id",
            "printify_image_index",
            "mask_image",
            "displacement_map",
            "shadow_layer",
            "highlight_layer",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {"base_image": {"required": False}}

    def validate(self, attrs):
        has_uploaded_file = attrs.get("base_image") is not None
        has_printify_selection = attrs.get("printify_blueprint_id") is not None and attrs.get("printify_image_index") is not None
        has_existing_image = bool(self.instance and self.instance.base_image)
        if not has_uploaded_file and not has_printify_selection and not has_existing_image:
            raise serializers.ValidationError(
                {"base_image": "Upload a file or select a Printify catalog image."}
            )
        return attrs

    def _resolve_printify_image_url(self, validated_data: dict) -> str | None:
        blueprint_id = validated_data.pop("printify_blueprint_id", None)
        image_index = validated_data.pop("printify_image_index", None)
        if blueprint_id is None or image_index is None:
            return None

        from apps.printify.models import PrintifyBlueprint

        try:
            blueprint = PrintifyBlueprint.objects.get(pk=blueprint_id)
        except PrintifyBlueprint.DoesNotExist:
            raise serializers.ValidationError({"printify_blueprint_id": "No such Printify blueprint."})

        images = blueprint.images or []
        if not isinstance(image_index, int) or not (0 <= image_index < len(images)):
            raise serializers.ValidationError({"printify_image_index": "Invalid image index for this blueprint."})

        url = images[image_index]
        if not isinstance(url, str) or not url.startswith("https://"):
            raise serializers.ValidationError({"printify_image_index": "This catalogue image entry isn't a usable URL."})
        return url

    def _apply_printify_image(self, instance: MockupTemplatePartColorAsset, image_url: str) -> None:
        from .services import UploadValidationError, fetch_base_image_from_printify_url

        try:
            filename, content = fetch_base_image_from_printify_url(image_url)
        except UploadValidationError as exc:
            raise serializers.ValidationError({"printify_image_index": str(exc)})
        instance.base_image.save(filename, content, save=False)

    def create(self, validated_data):
        image_url = self._resolve_printify_image_url(validated_data)
        instance = MockupTemplatePartColorAsset(**validated_data)
        if image_url:
            self._apply_printify_image(instance, image_url)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        image_url = self._resolve_printify_image_url(validated_data)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if image_url:
            self._apply_printify_image(instance, image_url)
        instance.save()
        return instance


class AdminMockupTemplateSerializer(serializers.ModelSerializer):
    """No template-level base_image/mask_image/displacement_map/shadow_layer/highlight_layer/
    config/canvas_width/canvas_height fields — every renderable surface is a
    MockupTemplatePart now, managed separately via AdminMockupTemplatePartListCreateView (the
    Parts tab in the admin panel), never a template-level "root" image. `is_active` mirrors
    MockupTemplate.clean(): a template can't be activated with zero parts."""

    parts = AdminMockupTemplatePartSerializer(many=True, read_only=True)

    class Meta:
        model = MockupTemplate
        fields = (
            "id",
            "name",
            "slug",
            "product_type",
            "description",
            "is_active",
            "template_version",
            "supported_colors",
            "supported_sizes",
            "supported_file_formats",
            "selected_print_provider",
            "parts",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        is_active = attrs.get("is_active", getattr(self.instance, "is_active", False))
        if is_active:
            parts_count = self.instance.parts.count() if self.instance else 0
            if parts_count == 0:
                raise serializers.ValidationError(
                    {"is_active": "A template must have at least one part before it can be activated."}
                )

        # "Select Provider" (Printify admin section 5): a plain ModelSerializer.save() never runs
        # MockupTemplate.clean() — only Django Admin's form does — so without this, a PATCH here
        # could silently set a provider that belongs to a different blueprint than the one mapped
        # to this template. Only validated when the client actually sends this field; leaving it
        # untouched on an unrelated field update never re-validates an existing, already-invalid
        # value into a hard error on an unrelated save.
        if "selected_print_provider" in attrs:
            from apps.printify.validation import validate_provider_matches_template_blueprint

            try:
                validate_provider_matches_template_blueprint(
                    self.instance.pk if self.instance else None, attrs["selected_print_provider"]
                )
            except ValueError as exc:
                raise serializers.ValidationError({"selected_print_provider": str(exc)})

        return attrs


class AdminProductVariantSerializer(serializers.ModelSerializer):
    """`readiness_status` is one of SELLABLE / MISSING_COST / UNAVAILABLE / INVALID_MAPPING —
    checked in that order against the exact same rule apps.shop.services.variant_is_sellable()
    already enforces (never re-derived here), just broken out into *which* check failed instead
    of a single boolean, so the admin table can show a specific reason rather than only
    'not sellable'. `product_name`/`provider_title` are read-only display conveniences —
    `product`/`template` stay the writable FK ids."""

    product_name = serializers.CharField(source="product.name", read_only=True)
    provider_title = serializers.SerializerMethodField()
    readiness_status = serializers.SerializerMethodField()
    is_sellable = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "product",
            "product_name",
            "template",
            "sku",
            "name",
            "color_name",
            "color_hex",
            "size",
            "external_provider",
            "external_variant_id",
            "provider_title",
            "base_cost",
            "retail_price",
            "inventory",
            "is_available",
            "is_sellable",
            "readiness_status",
            "image",
            "supported_print_areas",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_provider_title(self, obj: ProductVariant):
        provider = getattr(obj.template, "selected_print_provider", None)
        return provider.title if provider else None

    def get_readiness_status(self, obj: ProductVariant):
        if not obj.is_available:
            return "unavailable"
        if obj.base_cost is None:
            return "missing_cost"
        mockup_template_id = self.context.get("mockup_template_id", obj.product.mockup_template_id)
        if mockup_template_id and obj.template_id != mockup_template_id:
            return "invalid_mapping"
        if obj.external_provider and not obj.external_variant_id:
            return "invalid_mapping"
        return "sellable"

    def get_is_sellable(self, obj: ProductVariant):
        return self.get_readiness_status(obj) == "sellable"


class AdminGenerationRequestSerializer(serializers.ModelSerializer):
    """Support & Monitoring — read-only, admin-wide (no per-user scoping)."""

    user = serializers.SerializerMethodField()

    class Meta:
        model = GenerationRequest
        fields = (
            "id",
            "user",
            "prompt",
            "style",
            "provider",
            "model_name",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_user(self, obj: GenerationRequest):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None


class AdminGeneratedImageSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedImage
        fields = ("id", "user", "generation_request", "prompt", "image", "image_url", "created_at")
        read_only_fields = fields

    def get_user(self, obj: GeneratedImage):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None

    def get_image(self, obj: GeneratedImage):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None


class AdminSourceDesignAssetSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = SourceDesignAsset
        fields = (
            "id",
            "artwork",
            "owner",
            "source_type",
            "title",
            "source_url",
            "image",
            "width",
            "height",
            "mime_type",
            "file_size",
            "has_transparency",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_owner(self, obj: SourceDesignAsset):
        return {"id": obj.owner_id, "username": obj.owner.username} if obj.owner_id else None

    def get_image(self, obj: SourceDesignAsset):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None


class AdminMockupRenderSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    output_image = serializers.SerializerMethodField()

    class Meta:
        model = MockupRender
        fields = (
            "id",
            "user",
            "template",
            "part_name",
            "variant_color",
            "variant_size",
            "status",
            "output_image",
            "output_image_url",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_user(self, obj: MockupRender):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None

    def get_output_image(self, obj: MockupRender):
        if not obj.output_image:
            return None
        try:
            return obj.output_image.url
        except Exception:
            return None


class AdminGeneratedPrintFileSerializer(serializers.ModelSerializer):
    """Support & Monitoring — audit visibility only, mirrors Django admin's own read-only
    treatment of GeneratedPrintFile (never manually created/edited, only produced by the render
    pipeline — see apps.generator.services.create_or_reuse_print_file)."""

    output_file = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedPrintFile
        fields = (
            "id",
            "design_placement",
            "template_part",
            "output_file",
            "width",
            "height",
            "dpi",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_output_file(self, obj: GeneratedPrintFile):
        if not obj.output_file:
            return None
        try:
            return obj.output_file.url
        except Exception:
            return None


class AdminDesignProjectSerializer(serializers.ModelSerializer):
    """Admin-wide, read-only visibility into any user's design projects — for Support &
    Monitoring, distinct from the owner-scoped DesignProjectListCreateView/DesignProjectDetailView."""

    user = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    mockup_template_name = serializers.CharField(source="mockup_template.name", read_only=True)

    class Meta:
        model = DesignProject
        fields = (
            "id",
            "user",
            "name",
            "status",
            "product_name",
            "mockup_template_name",
            "selected_color",
            "selected_size",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_user(self, obj: DesignProject):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None

    def get_product_name(self, obj: DesignProject):
        return obj.product.name if obj.product_id else None

        return attrs
