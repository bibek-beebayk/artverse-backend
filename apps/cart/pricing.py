"""The server-side pricing engine (TODO.md item 27): the ONLY place unit prices and cart totals
are computed. Pure, side-effect-free, DB-read-only (queries only, no writes) — every function
here is directly unit-testable without mocking anything.

Formula (TODO.md item 27's wording, mapped onto what actually exists in this codebase):

    Printify base cost              -> variant.base_cost or 0 (ProductVariant, admin-set — see
                                        apps.printify.services.sync_product_variants_from_printify's
                                        own docstring: Printify's catalogue doesn't reliably
                                        return retail-ready cost data, so this is never synced
                                        automatically and is genuinely 0 until an admin sets it)
  + printing-area charges           -> PrintAreaCharge, summed over every part with printable
  + additional sleeve/back charges     content. These are the same mechanism: nothing else in
                                        this codebase distinguishes "a printing-area charge" from
                                        "a sleeve/back charge" — see PrintAreaCharge's docstring.
  + platform markup                 -> resolve_markup_rule()'s single most-specific matching
                                        PricingRule, applied to (base cost + print-area charges)
  = CartItem.unit_price              (price_item(), per unit)

    subtotal = sum(unit_price * quantity)
  - discounts                       -> the cart's Coupon, if any (validate_coupon())
  + taxes                           -> PricingConfig.tax_percentage, applied to the
                                        POST-DISCOUNT subtotal
  + shipping                        -> PricingConfig.flat_shipping_amount, waived at/above
                                        free_shipping_threshold (also on a post-discount basis)
  = customer total                   (price_cart_totals(), once per cart)

Tax and shipping are deliberately simple, admin-configurable flat-rate placeholders, not real
tax-jurisdiction or shipping-rate-shopping calculation — real shipping-rate calculation by
provider/destination/quantity is TODO.md item 31, a separate later task. No tax-jurisdiction
service is anywhere in the roadmap.

Never trust a price submitted by the frontend: apps.cart.services always calls price_item() to
set CartItem.unit_price server-side, ignoring any client-submitted price field entirely.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from django.db import models as django_models
from django.utils import timezone

from apps.generator.models import DesignPlacement, ProductVariant
from apps.generator.services import is_placement_printable
from apps.shop.models import Product

from .models import Coupon, PricingConfig, PricingRule, PrintAreaCharge

TWO_PLACES = Decimal("0.01")


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class CouponError(ValueError):
    """Raised by validate_coupon() with a human-readable, user-facing message."""


@dataclass(frozen=True)
class ItemPricingBreakdown:
    base_cost: Decimal
    print_area_charges: Decimal
    markup_amount: Decimal
    markup_rule_id: Optional[int]
    unit_price: Decimal
    warnings: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class CartPricingBreakdown:
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    total: Decimal
    currency: str


def resolve_markup_rule(*, product: Product, now: Optional[datetime] = None) -> Optional[PricingRule]:
    """Precedence: product-specific > category > global. Only the single most specific ACTIVE,
    currently-in-window rule applies — rules are never stacked (see PricingRule's docstring for
    why). Ties within the same rule_type break on (priority desc, updated_at desc)."""
    now = now or timezone.now()

    def _active(queryset):
        return (
            queryset.filter(is_active=True)
            .filter(django_models.Q(starts_at__isnull=True) | django_models.Q(starts_at__lte=now))
            .filter(django_models.Q(ends_at__isnull=True) | django_models.Q(ends_at__gte=now))
            .order_by("-priority", "-updated_at")
        )

    product_rule = _active(
        PricingRule.objects.filter(rule_type=PricingRule.RuleType.PRODUCT, product=product)
    ).first()
    if product_rule:
        return product_rule

    if product.category_id:
        category_rule = _active(
            PricingRule.objects.filter(rule_type=PricingRule.RuleType.CATEGORY, category_id=product.category_id)
        ).first()
        if category_rule:
            return category_rule

    return _active(PricingRule.objects.filter(rule_type=PricingRule.RuleType.GLOBAL)).first()


def price_item(
    *,
    product: Product,
    variant: Optional[ProductVariant],
    placements: Iterable[DesignPlacement],
    now: Optional[datetime] = None,
) -> ItemPricingBreakdown:
    warnings = []

    if variant is not None and variant.base_cost is not None:
        base_cost = variant.base_cost
    else:
        base_cost = Decimal("0.00")
        warnings.append("No variant base_cost set — defaulted to 0.00. An admin should set ProductVariant.base_cost.")

    printable_parts = {
        placement.part_name for placement in placements if is_placement_printable(placement) or placement.text_elements
    }
    charge_rows = {
        row.part_name: row.amount
        for row in PrintAreaCharge.objects.filter(part_name__in=printable_parts, is_active=True)
    }
    print_area_charges = sum((charge_rows.get(part, Decimal("0.00")) for part in printable_parts), Decimal("0.00"))

    rule = resolve_markup_rule(product=product, now=now)
    production_cost = base_cost + print_area_charges
    if rule is None:
        markup_amount = Decimal("0.00")
    elif rule.markup_type == PricingRule.MarkupType.FIXED:
        markup_amount = rule.amount
    else:
        markup_amount = _round(production_cost * rule.amount / Decimal("100"))

    unit_price = _round(base_cost + print_area_charges + markup_amount)

    return ItemPricingBreakdown(
        base_cost=_round(base_cost),
        print_area_charges=_round(print_area_charges),
        markup_amount=_round(markup_amount),
        markup_rule_id=rule.id if rule else None,
        unit_price=unit_price,
        warnings=tuple(warnings),
    )


def validate_coupon(*, coupon: Optional[Coupon], subtotal: Decimal, user) -> Coupon:
    """Raises CouponError with a user-facing message on any failure. Does NOT mutate
    times_redeemed — that only ever happens at order completion (Section 5, not built here)."""
    if coupon is None:
        raise CouponError("Invalid coupon code.")
    if not coupon.is_active:
        raise CouponError("This coupon is no longer active.")
    now = timezone.now()
    if coupon.starts_at and now < coupon.starts_at:
        raise CouponError("This coupon isn't active yet.")
    if coupon.ends_at and now > coupon.ends_at:
        raise CouponError("This coupon has expired.")
    if coupon.min_subtotal is not None and subtotal < coupon.min_subtotal:
        raise CouponError(f"Add {coupon.min_subtotal - subtotal:.2f} more to your cart to use this coupon.")
    if coupon.max_redemptions is not None and coupon.times_redeemed >= coupon.max_redemptions:
        raise CouponError("This coupon has reached its usage limit.")
    # max_redemptions_per_user: enforced against real completed-order redemptions, which don't
    # exist yet (no Order model in this pass) — the field exists for Section 5 to check against;
    # today it is intentionally a no-op (nothing tracks per-user redemption yet).
    return coupon


def price_cart_totals(*, subtotal: Decimal, coupon: Optional[Coupon], config: PricingConfig) -> CartPricingBreakdown:
    if coupon is not None:
        if coupon.discount_type == Coupon.DiscountType.FIXED:
            discount_amount = coupon.amount
        else:
            discount_amount = _round(subtotal * coupon.amount / Decimal("100"))
        discount_amount = min(discount_amount, subtotal)
    else:
        discount_amount = Decimal("0.00")

    discounted_subtotal = subtotal - discount_amount
    tax_amount = _round(discounted_subtotal * config.tax_percentage / Decimal("100"))

    if config.free_shipping_threshold is not None and discounted_subtotal >= config.free_shipping_threshold:
        shipping_amount = Decimal("0.00")
    else:
        shipping_amount = config.flat_shipping_amount

    total = discounted_subtotal + tax_amount + shipping_amount

    return CartPricingBreakdown(
        subtotal=_round(subtotal),
        discount_amount=_round(discount_amount),
        tax_amount=_round(tax_amount),
        shipping_amount=_round(shipping_amount),
        total=_round(total),
        currency=config.currency,
    )
