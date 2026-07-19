from django.contrib import admin

from .models import Cart, CartItem, Coupon, PricingConfig, PricingRule, PrintAreaCharge


@admin.register(PricingConfig)
class PricingConfigAdmin(admin.ModelAdmin):
    """Singleton — mirrors apps.accounts.admin.SiteConfigurationAdmin exactly."""

    fieldsets = (
        (
            "Global Pricing",
            {"fields": ("currency", "tax_percentage", "flat_shipping_amount", "free_shipping_threshold", "updated_at")},
        ),
    )
    readonly_fields = ("updated_at",)
    list_display = ("currency", "tax_percentage", "flat_shipping_amount", "free_shipping_threshold", "updated_at")

    def has_add_permission(self, request):
        if PricingConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrintAreaCharge)
class PrintAreaChargeAdmin(admin.ModelAdmin):
    list_display = ("part_name", "amount", "currency", "is_active", "updated_at")
    list_filter = ("is_active",)


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "rule_type", "markup_type", "amount", "category", "product", "priority", "is_active")
    list_filter = ("rule_type", "markup_type", "is_active")
    search_fields = ("name", "product__name", "category__name")
    list_select_related = ("category", "product")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "amount", "is_active", "times_redeemed", "max_redemptions", "ends_at")
    list_filter = ("is_active", "discount_type")
    search_fields = ("code",)
    readonly_fields = ("times_redeemed", "created_at", "updated_at")


class CartItemInline(admin.TabularInline):
    """Read-only — cart items are only ever created/mutated through the API (apps.cart.services),
    never hand-edited, since unit_price is a pricing-engine output, not admin input."""

    model = CartItem
    extra = 0
    fields = ("design_project", "product", "variant", "quantity", "unit_price", "currency", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """Read-only — support/debugging only, mirroring GeneratedPrintFileAdmin's pattern."""

    list_display = ("id", "user", "currency", "coupon", "updated_at")
    search_fields = ("user__email",)
    list_select_related = ("user", "coupon")
    inlines = [CartItemInline]
    readonly_fields = [f.name for f in Cart._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return True
