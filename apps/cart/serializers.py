from rest_framework import serializers

from apps.generator.serializers import DesignProjectListSerializer
from apps.shop.models import Product

from .models import Cart, CartItem, Coupon
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
            "created_at",
            "updated_at",
        )

    def get_line_total(self, obj: CartItem):
        return str(obj.unit_price * obj.quantity)


class CouponSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ("code", "discount_type", "amount")


def serialize_cart(cart: Cart) -> dict:
    """The single place that assembles a full cart response — always re-prices first (via
    recompute_cart), so a stale admin-side price/rule/coupon change is reflected the moment a
    user looks at their cart, not just after their next mutation."""
    items, totals = recompute_cart(cart)
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
        "updated_at": cart.updated_at,
    }
