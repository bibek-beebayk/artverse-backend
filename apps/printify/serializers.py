from rest_framework import serializers

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun


class PrintifyPrintProviderSerializer(serializers.ModelSerializer):
    """Backs the Blueprints page's provider-inspection panel — every field here comes from
    already-synced local data (this provider's own `variants` JSON, or a bounded query against
    `ProductVariant` rows this provider currently fulfils), never a live Printify request."""

    variant_count = serializers.SerializerMethodField()
    available_variant_count = serializers.SerializerMethodField()
    supported_placeholders = serializers.SerializerMethodField()
    missing_cost_variant_count = serializers.SerializerMethodField()
    missing_external_id_variant_count = serializers.SerializerMethodField()

    class Meta:
        model = PrintifyPrintProvider
        fields = (
            "id",
            "provider_id",
            "title",
            "location",
            "variant_count",
            "available_variant_count",
            "supported_placeholders",
            "missing_cost_variant_count",
            "missing_external_id_variant_count",
            "synced_at",
        )

    def get_variant_count(self, obj: PrintifyPrintProvider) -> int:
        return len(obj.variants or [])

    def get_available_variant_count(self, obj: PrintifyPrintProvider) -> int:
        # `is_enabled` is a synthetic field folded in by
        # services.fetch_print_provider_variants() — see that function's docstring for why
        # Printify's raw catalogue has no native "in stock" flag to read directly.
        return sum(1 for v in (obj.variants or []) if isinstance(v, dict) and v.get("is_enabled"))

    def get_supported_placeholders(self, obj: PrintifyPrintProvider):
        from .validation import get_provider_placeholder_positions

        return sorted(get_provider_placeholder_positions(obj))

    def get_missing_cost_variant_count(self, obj: PrintifyPrintProvider) -> int:
        # Only ever non-zero once this provider has actually been selected on a template and
        # variants synced from it — inspecting a not-yet-selected provider correctly reports 0
        # ("nothing synced from it yet"), not an error.
        from apps.generator.models import ProductVariant

        return ProductVariant.objects.filter(template__selected_print_provider=obj, base_cost__isnull=True).count()

    def get_missing_external_id_variant_count(self, obj: PrintifyPrintProvider) -> int:
        from apps.generator.models import ProductVariant

        return (
            ProductVariant.objects.filter(template__selected_print_provider=obj, external_variant_id="")
            .exclude(external_provider="")
            .count()
        )


class PrintifyBlueprintListSerializer(serializers.ModelSerializer):
    is_mapped = serializers.SerializerMethodField()
    provider_count = serializers.SerializerMethodField()

    class Meta:
        model = PrintifyBlueprint
        fields = (
            "id",
            "blueprint_id",
            "title",
            "brand",
            "model",
            "images",
            "mockup_template",
            "is_mapped",
            "provider_count",
            "synced_at",
        )

    def get_is_mapped(self, obj: PrintifyBlueprint) -> bool:
        return obj.mockup_template_id is not None

    def get_provider_count(self, obj: PrintifyBlueprint) -> int:
        # Views annotate `provider_count_value` via Count("print_providers") so the list/detail
        # endpoints don't issue one COUNT query per row. Fall back to a direct query only when
        # this serializer is used against a non-annotated object (e.g. PrintifyBlueprintMapView,
        # which fetches a single blueprint by pk without annotating it).
        annotated = getattr(obj, "provider_count_value", None)
        if annotated is not None:
            return annotated
        return obj.print_providers.count()


class PrintifyBlueprintDetailSerializer(PrintifyBlueprintListSerializer):
    print_providers = PrintifyPrintProviderSerializer(many=True, read_only=True)

    class Meta(PrintifyBlueprintListSerializer.Meta):
        fields = PrintifyBlueprintListSerializer.Meta.fields + ("description", "print_providers")


class PrintifySyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintifySyncRun
        fields = (
            "id",
            "kind",
            "status",
            "blueprint",
            "blueprints_synced",
            "providers_synced",
            "variants_synced",
            "error_message",
            "started_at",
            "finished_at",
        )


class PrintifyBlueprintMapSerializer(serializers.Serializer):
    mockup_template_id = serializers.IntegerField(allow_null=True)

    def validate_mockup_template_id(self, value):
        if value is None:
            return value
        from apps.generator.models import MockupTemplate

        if not MockupTemplate.objects.filter(pk=value).exists():
            raise serializers.ValidationError("No mockup template with this ID exists.")
        return value
