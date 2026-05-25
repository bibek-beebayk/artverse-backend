from rest_framework import serializers

from .models import GeneratedImage, GenerationRequest, MockupRender, MockupTemplate


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


class MockupTemplateSerializer(serializers.ModelSerializer):
    base_image = serializers.SerializerMethodField()
    mask_image = serializers.SerializerMethodField()
    displacement_map = serializers.SerializerMethodField()
    shadow_layer = serializers.SerializerMethodField()
    highlight_layer = serializers.SerializerMethodField()
    product_type_display = serializers.CharField(source="get_product_type_display", read_only=True)

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
            "base_image",
            "mask_image",
            "displacement_map",
            "shadow_layer",
            "highlight_layer",
            "template_version",
            "config",
            "supported_colors",
            "supported_sizes",
            "updated_at",
        )

    def _get_file_url(self, file_field):
        if not file_field:
            return None
        try:
            return file_field.url
        except Exception:
            return None

    def get_base_image(self, obj: MockupTemplate):
        return self._get_file_url(obj.base_image)

    def get_mask_image(self, obj: MockupTemplate):
        return self._get_file_url(obj.mask_image)

    def get_displacement_map(self, obj: MockupTemplate):
        return self._get_file_url(obj.displacement_map)

    def get_shadow_layer(self, obj: MockupTemplate):
        return self._get_file_url(obj.shadow_layer)

    def get_highlight_layer(self, obj: MockupTemplate):
        return self._get_file_url(obj.highlight_layer)


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
            "variant_color",
            "variant_size",
            "placement_override",
            "crop_override",
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
    variant_color = serializers.CharField(required=False, allow_blank=True, max_length=120)
    variant_size = serializers.CharField(required=False, allow_blank=True, max_length=120)
    placement_override = serializers.JSONField(required=False)
    crop_override = serializers.JSONField(required=False)

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
