from django.contrib import admin, messages
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.generator.models import ProductVariant
from apps.printify.services import PrintifyError, sync_product_variants_from_printify

from .models import NotificationSubscription, Product, ProductCategory
from .services import (
    activate_product,
    deactivate_product,
    get_product_starting_price,
    product_has_sellable_variant,
    validate_product_can_be_activated,
)


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ShopProductVariantInline(admin.TabularInline):
    model = ProductVariant
    fk_name = "product"
    extra = 0
    fields = (
        "template",
        "sku",
        "color_name",
        "color_hex",
        "size",
        "base_cost",
        "retail_price",
        "inventory",
        "is_available",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Lifecycle: create a draft (is_active=False, zero variants allowed) -> add/sync variants
    -> configure base_cost/is_available on them -> activate. Activation always goes through
    apps.shop.services.activate_product() — never toggle is_active=True by hand-editing the
    field and trusting it, since that bypasses the sellable-variant check entirely (Django Admin
    itself doesn't call Product.clean() with the inline formset's just-submitted data — see
    save_model()/save_related() below for why activation is deferred to *after* inlines save)."""

    list_display = (
        "id",
        "name",
        "category",
        "mockup_template",
        "starting_price_display",
        "available_variant_count_display",
        "is_active",
    )
    list_filter = ("category", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    inlines = [ShopProductVariantInline]
    actions = ["sync_variants_from_printify", "activate_selected", "deactivate_selected"]
    readonly_fields = (
        "starting_price_display",
        "available_variant_count_display",
        "total_variant_count_display",
        "activation_readiness_display",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("name", "slug", "category", "description", "is_active")}),
        ("Images", {"fields": ("image", "thumbnail", "image_url")}),
        ("Customization", {"fields": ("mockup_template",)}),
        (
            "Derived (read-only)",
            {
                "fields": (
                    "starting_price_display",
                    "available_variant_count_display",
                    "total_variant_count_display",
                    "activation_readiness_display",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Starting price")
    def starting_price_display(self, obj: Product):
        if not obj.pk:
            return "—"
        price = get_product_starting_price(obj)
        return f"${price}" if price is not None else "No valid price yet"

    @admin.display(description="Available variants")
    def available_variant_count_display(self, obj: Product):
        if not obj.pk:
            return "—"
        return obj.variants.filter(is_available=True, base_cost__isnull=False).count()

    @admin.display(description="Total variants")
    def total_variant_count_display(self, obj: Product):
        if not obj.pk:
            return "—"
        return obj.variants.count()

    @admin.display(description="Activation readiness")
    def activation_readiness_display(self, obj: Product):
        if not obj.pk:
            return "Save the product first"
        try:
            validate_product_can_be_activated(obj)
        except DjangoValidationError as exc:
            return "Not ready: " + " ".join(exc.messages)
        return "Ready to activate" if not obj.is_active else "Active and valid"

    def save_model(self, request, obj, form, change):
        # Inline variant formsets haven't been saved yet at this point in Django Admin's flow
        # (save_model runs before save_related) — activating here would validate against
        # whatever variants existed *before* this request, not the ones just submitted alongside
        # it. So: force inactive now if activation was requested, and do the real
        # validate-then-activate in save_related() below, once the inlines are actually
        # persisted. This never loses the inline edits themselves even if activation fails —
        # only `is_active` stays False.
        was_active = bool(change and form.initial.get("is_active"))
        self._activation_requested = bool(obj.is_active) and not was_active
        if self._activation_requested:
            obj.is_active = False
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if getattr(self, "_activation_requested", False):
            self._activation_requested = False
            try:
                activate_product(form.instance)
                self.message_user(request, f"{form.instance.name}: activated.", level=messages.SUCCESS)
            except DjangoValidationError as exc:
                self.message_user(
                    request,
                    f"{form.instance.name}: could not activate — {' '.join(exc.messages)}",
                    level=messages.ERROR,
                )

    @admin.action(description="Activate selected products")
    def activate_selected(self, request, queryset):
        succeeded = failed = 0
        for product in queryset:
            try:
                activate_product(product)
                succeeded += 1
            except DjangoValidationError as exc:
                failed += 1
                self.message_user(request, f"{product.name}: {' '.join(exc.messages)}", level=messages.ERROR)
        if succeeded:
            self.message_user(request, f"Activated {succeeded} product(s).", level=messages.SUCCESS)
        if failed:
            self.message_user(
                request, f"{failed} product(s) could not be activated — see errors above.", level=messages.WARNING
            )

    @admin.action(description="Deactivate selected products")
    def deactivate_selected(self, request, queryset):
        count = 0
        for product in queryset:
            deactivate_product(product)
            count += 1
        self.message_user(request, f"Deactivated {count} product(s).", level=messages.SUCCESS)

    @admin.action(description="Sync variants from mapped Printify print provider")
    def sync_variants_from_printify(self, request, queryset):
        for product in queryset:
            try:
                summary = sync_product_variants_from_printify(product)
            except PrintifyError as exc:
                self.message_user(request, f"{product.name}: {exc}", level=messages.ERROR)
                continue
            message = (
                f"{product.name}: {summary['created']} created, {summary['updated']} updated, "
                f"{summary['unavailable']} marked unavailable"
            )
            if summary.get("missing_cost"):
                message += f", {summary['missing_cost']} still need a production cost configured"
            self.message_user(
                request,
                message + ".",
                level=messages.SUCCESS if product_has_sellable_variant(product) else messages.WARNING,
            )


@admin.register(NotificationSubscription)
class NotificationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "email", "user", "created_at")
    search_fields = ("email", "product__name")
