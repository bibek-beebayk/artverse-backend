from django.contrib import admin, messages

from apps.generator.models import ProductVariant
from apps.printify.services import PrintifyError, sync_product_variants_from_printify

from .models import NotificationSubscription, Product, ProductCategory


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
    list_display = ("id", "name", "category", "mockup_template", "price", "inventory", "is_active")
    list_filter = ("category", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    inlines = [ShopProductVariantInline]
    actions = ["sync_variants_from_printify"]

    @admin.action(description="Sync variants from mapped Printify print provider")
    def sync_variants_from_printify(self, request, queryset):
        for product in queryset:
            try:
                summary = sync_product_variants_from_printify(product)
            except PrintifyError as exc:
                self.message_user(request, f"{product.name}: {exc}", level=messages.ERROR)
                continue
            self.message_user(
                request,
                f"{product.name}: {summary['created']} created, {summary['updated']} updated, "
                f"{summary['marked_unavailable']} marked unavailable.",
                level=messages.SUCCESS,
            )


@admin.register(NotificationSubscription)
class NotificationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "email", "user", "created_at")
    search_fields = ("email", "product__name")
