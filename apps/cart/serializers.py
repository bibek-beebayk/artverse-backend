from rest_framework import serializers

from apps.generator.serializers import DesignProjectListSerializer
from apps.shop.models import Product

from .models import Cart, CartItem, Coupon, PricingConfig, PricingRule, PrintAreaCharge
from .services import recompute_cart


class CartItemProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "slug", "image_url")


class CartItemSerializer(serializers.ModelSerializer):
    design_project = DesignProjectListSerializer(read_only=True)
    product = CartItemProductSerializer(read_only=True)
    variant_id = serializers.IntegerField(source="variant.id", read_only=True, default=None)
    line_total = serializers.SerializerMethodField()
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = (
            "id",
            "design_project",
            "product",
            "variant_id",
            "size",
            "colour",
            "quantity",
            "unit_price",
            "currency",
            "preview_image_url",
            "line_total",
            "pricing_breakdown",
            "warnings",
            "created_at",
            "updated_at",
        )

    def get_line_total(self, obj: CartItem):
        return str(obj.unit_price * obj.quantity)

    def get_warnings(self, obj: CartItem):
        # Surfaced as its own top-level field (rather than making every consumer dig into the
        # pricing_breakdown JSON blob) — see pricing.price_item()'s warnings list. Currently:
        # missing ProductVariant.base_cost, or a variant that's no longer available.
        return list(obj.pricing_breakdown.get("warnings", []))


class CouponSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ("code", "discount_type", "amount")


def serialize_cart(cart: Cart) -> dict:
    """The single place that assembles a full cart response — always re-prices first (via
    recompute_cart), so a stale admin-side price/rule/coupon change is reflected the moment a
    user looks at their cart, not just after their next mutation."""
    items, totals = recompute_cart(cart)
    has_warnings = any(item.pricing_breakdown.get("warnings") for item in items)
    return {
        "id": cart.id,
        "currency": cart.currency,
        "items": CartItemSerializer(items, many=True).data,
        "coupon": CouponSummarySerializer(cart.coupon).data if cart.coupon else None,
        "subtotal": str(totals.subtotal),
        "discount_amount": str(totals.discount_amount),
        "tax_amount": str(totals.tax_amount),
        "shipping_amount": str(totals.shipping_amount),
        "total": str(totals.total),
        # False whenever any item carries a pricing/availability warning (see
        # CartItemSerializer.get_warnings) — real checkout doesn't exist yet regardless (see
        # CartPage.tsx's isCheckoutImplemented), but this is what a future checkout flow should
        # gate on, and it's what the frontend uses today to decide which "not ready yet"
        # message to show.
        "is_checkout_ready": not has_warnings,
        "updated_at": cart.updated_at,
    }


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminPricingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingConfig
        fields = (
            "currency",
            "tax_percentage",
            "flat_shipping_amount",
            "free_shipping_threshold",
            "updated_at",
        )
        read_only_fields = ("updated_at",)


class AdminPrintAreaChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintAreaCharge
        fields = ("id", "part_name", "amount", "currency", "is_active", "updated_at")
        read_only_fields = ("id", "updated_at")


class AdminPricingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingRule
        fields = (
            "id",
            "name",
            "rule_type",
            "markup_type",
            "amount",
            "category",
            "product",
            "currency",
            "priority",
            "is_active",
            "starts_at",
            "ends_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        # Mirrors PricingRule.clean() (CheckConstraint scope-matches-rule_type) as a friendly
        # DRF validation error instead of letting an invalid combination reach the DB constraint.
        rule_type = attrs.get("rule_type", getattr(self.instance, "rule_type", None))
        category = attrs.get("category", getattr(self.instance, "category", None))
        product = attrs.get("product", getattr(self.instance, "product", None))
        if rule_type == PricingRule.RuleType.CATEGORY and not category:
            raise serializers.ValidationError({"category": "Category markup rules require a category."})
        if rule_type == PricingRule.RuleType.PRODUCT and not product:
            raise serializers.ValidationError({"product": "Product-specific markup rules require a product."})
        if rule_type == PricingRule.RuleType.GLOBAL and (category or product):
            raise serializers.ValidationError(
                {"rule_type": "Global markup rules must not set a category or product."}
            )
        return attrs


class AdminCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = (
            "id",
            "code",
            "discount_type",
            "amount",
            "currency",
            "is_active",
            "starts_at",
            "ends_at",
            "min_subtotal",
            "max_redemptions",
            "max_redemptions_per_user",
            "times_redeemed",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "times_redeemed", "created_at", "updated_at")


class AdminCartSerializer(serializers.ModelSerializer):
    """Support & Monitoring — read-only, admin-wide. A lightweight summary (item count, coupon
    code), not the fully re-priced serialize_cart() payload above — that's an expensive
    per-cart recompute meant for a single owner's own cart, not a paginated admin list."""

    user = serializers.SerializerMethodField()
    coupon_code = serializers.CharField(source="coupon.code", read_only=True, default=None)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ("id", "user", "currency", "coupon_code", "item_count", "created_at", "updated_at")
        read_only_fields = fields

    def get_user(self, obj: Cart):
        return {"id": obj.user_id, "username": obj.user.username} if obj.user_id else None

    def get_item_count(self, obj: Cart):
        return obj.items.count()
