from django.contrib import admin, messages

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun
from .services import PrintifyError, sync_print_providers_for_blueprint, validate_configured_shop


class PrintifyPrintProviderInline(admin.TabularInline):
    model = PrintifyPrintProvider
    extra = 0
    fields = ("provider_id", "title", "variant_count", "synced_at")
    readonly_fields = ("provider_id", "title", "variant_count", "synced_at")
    can_delete = False

    def variant_count(self, obj: PrintifyPrintProvider) -> int:
        return len(obj.variants or [])

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PrintifyBlueprint)
class PrintifyBlueprintAdmin(admin.ModelAdmin):
    list_display = ("blueprint_id", "title", "brand", "model", "mockup_template", "provider_count", "synced_at")
    list_filter = ("brand",)
    search_fields = ("title", "brand", "model")
    readonly_fields = ("blueprint_id", "title", "brand", "model", "description", "images", "raw_data", "synced_at", "created_at")
    fields = (
        "blueprint_id",
        "title",
        "brand",
        "model",
        "description",
        "images",
        "mockup_template",
        "raw_data",
        "synced_at",
        "created_at",
    )
    inlines = [PrintifyPrintProviderInline]
    actions = ["sync_print_providers"]

    def provider_count(self, obj: PrintifyBlueprint) -> int:
        return obj.print_providers.count()

    @admin.action(description="Sync print providers + variants from Printify for selected blueprints")
    def sync_print_providers(self, request, queryset):
        try:
            validate_configured_shop()
        except PrintifyError as exc:
            self.message_user(request, f"Printify connection check failed: {exc}", level=messages.ERROR)
            return

        for blueprint in queryset:
            run = sync_print_providers_for_blueprint(blueprint, triggered_by=request.user)
            if run.status == PrintifySyncRun.Status.SUCCESS:
                self.message_user(
                    request,
                    f"{blueprint.title}: {run.providers_created} created, {run.providers_updated} updated "
                    f"({run.providers_synced} provider(s), {run.variants_synced} variant(s)).",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(request, f"{blueprint.title}: {run.error_message}", level=messages.ERROR)


@admin.register(PrintifyPrintProvider)
class PrintifyPrintProviderAdmin(admin.ModelAdmin):
    list_display = ("title", "provider_id", "blueprint", "variant_count", "synced_at")
    list_filter = ("blueprint",)
    search_fields = ("title", "blueprint__title")
    readonly_fields = ("blueprint", "provider_id", "title", "location", "variants", "raw_data", "synced_at", "created_at")

    def variant_count(self, obj: PrintifyPrintProvider) -> int:
        return len(obj.variants or [])

    def has_add_permission(self, request):
        return False


@admin.register(PrintifySyncRun)
class PrintifySyncRunAdmin(admin.ModelAdmin):
    list_display = ("kind", "status", "blueprint", "triggered_by", "started_at", "finished_at")
    list_filter = ("kind", "status")
    readonly_fields = [f.name for f in PrintifySyncRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
