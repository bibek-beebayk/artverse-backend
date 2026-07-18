from rest_framework import serializers

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun


class PrintifyPrintProviderSerializer(serializers.ModelSerializer):
    variant_count = serializers.SerializerMethodField()

    class Meta:
        model = PrintifyPrintProvider
        fields = ("id", "provider_id", "title", "location", "variant_count", "synced_at")

    def get_variant_count(self, obj: PrintifyPrintProvider) -> int:
        return len(obj.variants or [])


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
