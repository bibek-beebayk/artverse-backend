"""Cart orchestration — the only place that writes Cart/CartItem. All pricing math is delegated
to apps.cart.pricing; nothing here computes a price directly."""

from decimal import Decimal
from typing import TypedDict

from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.generator.models import DesignProject

from .models import Cart, CartItem, Coupon, PricingConfig
from .pricing import CartPricingBreakdown, CouponError, price_cart_totals, price_item, validate_coupon


def get_or_create_cart(user) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user, defaults={"currency": PricingConfig.get_solo().currency})
    return cart


def _reprice_item(item: CartItem) -> CartItem:
    breakdown = price_item(
        product=item.product,
        variant=item.variant,
        placements=item.design_project.placements.all(),
    )
    item.unit_price = breakdown.unit_price
    item.pricing_breakdown = {
        "base_cost": str(breakdown.base_cost),
        "print_area_charges": str(breakdown.print_area_charges),
        "markup_amount": str(breakdown.markup_amount),
        "markup_rule_id": breakdown.markup_rule_id,
        "warnings": list(breakdown.warnings),
    }
    return item


def recompute_cart(cart: Cart) -> tuple[list[CartItem], CartPricingBreakdown]:
    """Re-prices every item on every read — defends against stale prices if an admin changed
    base_cost/markup rules/print-area charges since an item was added. Called from every
    GET /api/cart/ and after every mutating call; there is no separate 'recompute' endpoint."""
    items = list(
        cart.items.select_related("product", "variant", "design_project").prefetch_related(
            "design_project__placements"
        )
    )
    for item in items:
        _reprice_item(item)
    if items:
        CartItem.objects.bulk_update(items, ["unit_price", "pricing_breakdown"])

    subtotal = sum((item.unit_price * item.quantity for item in items), Decimal("0.00"))
    config = PricingConfig.get_solo()
    totals = price_cart_totals(subtotal=subtotal, coupon=cart.coupon, config=config)
    return items, totals


class AddItemResult(TypedDict):
    item: CartItem
    created: bool


@transaction.atomic
def add_design_project_to_cart(*, user, design_project_id: int, quantity: int = 1) -> AddItemResult:
    """Never accepts product_id/variant_id/price from a caller — those are always read straight
    off the already-owner-verified DesignProject, never trusted from a request body."""
    design_project = get_object_or_404(DesignProject, pk=design_project_id, user=user)
    if design_project.product_id is None:
        raise ValueError("This design isn't linked to a product yet and can't be added to the cart.")

    cart = get_or_create_cart(user)
    quantity = max(1, quantity)

    existing = CartItem.objects.filter(cart=cart, design_project=design_project).first()
    if existing:
        existing.quantity += quantity
        _reprice_item(existing)
        existing.save(update_fields=["quantity", "unit_price", "pricing_breakdown", "updated_at"])
        return {"item": existing, "created": False}

    item = CartItem(
        cart=cart,
        design_project=design_project,
        product=design_project.product,
        variant=design_project.selected_variant,
        size=design_project.selected_size,
        colour=design_project.selected_color,
        quantity=quantity,
        preview_image_url=design_project.thumbnail_url or "",
        unit_price=Decimal("0.00"),
    )
    _reprice_item(item)
    item.save()

    if design_project.status == DesignProject.Status.DRAFT:
        design_project.status = DesignProject.Status.READY
        design_project.save(update_fields=["status"])

    return {"item": item, "created": True}


@transaction.atomic
def update_cart_item_quantity(*, user, item_id: int, quantity: int) -> CartItem:
    item = get_object_or_404(CartItem.objects.select_related("cart", "design_project"), pk=item_id, cart__user=user)
    item.quantity = max(1, quantity)
    _reprice_item(item)
    item.save(update_fields=["quantity", "unit_price", "pricing_breakdown", "updated_at"])
    return item


def remove_cart_item(*, user, item_id: int) -> None:
    get_object_or_404(CartItem.objects.select_related("cart"), pk=item_id, cart__user=user).delete()


def apply_coupon(*, user, code: str) -> Cart:
    cart = get_or_create_cart(user)
    _, totals = recompute_cart(cart)
    coupon = Coupon.objects.filter(code__iexact=code.strip()).first()
    validate_coupon(coupon=coupon, subtotal=totals.subtotal, user=user)  # raises CouponError
    cart.coupon = coupon
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


def remove_coupon(*, user) -> Cart:
    cart = get_or_create_cart(user)
    cart.coupon = None
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


class MergeEntry(TypedDict):
    design_project_id: int
    quantity: int


def merge_guest_cart(*, user, entries: list) -> dict:
    """One bad/stale guest entry (a design belonging to someone else, a deleted design, a
    design with no product) must not sink the whole merge — each entry is attempted
    independently and failures are collected, not raised."""
    merged = 0
    errors: list[dict] = []
    for entry in entries:
        design_project_id = entry.get("design_project_id")
        try:
            add_design_project_to_cart(
                user=user, design_project_id=design_project_id, quantity=max(1, int(entry.get("quantity", 1)))
            )
            merged += 1
        except Exception as exc:
            errors.append({"design_project_id": design_project_id, "error": str(exc)})
    return {"merged": merged, "errors": errors}
