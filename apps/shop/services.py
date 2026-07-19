"""Derived product pricing/availability/activation logic — Product itself carries no price,
inventory, or second availability flag; everything sellable is computed from its
`ProductVariant` rows (`apps.generator.models.ProductVariant`, imported locally throughout this
module to avoid a module-load-time circular import between `apps.shop` and `apps.generator`/
`apps.cart` — both of which already import `apps.shop.models.Product` at their own module top).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q

TWO_PLACES = Decimal("0.01")


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _is_variant_sellable(variant, *, mockup_template_id: Optional[int]) -> bool:
    """The single definition of "sellable" a variant must meet — mirrored (not literally
    shared, since one runs on already-loaded Python objects and the other as a correlated SQL
    subquery) by `sellable_variant_exists_subquery()` below. Keep the two in sync if this
    changes."""
    if not variant.is_available or variant.base_cost is None:
        return False
    if mockup_template_id and variant.template_id != mockup_template_id:
        return False
    # "Valid provider mapping where required": a variant claiming an external provider needs a
    # real provider-side id; a purely local/manual variant (no external_provider set) has
    # nothing to validate here.
    if variant.external_provider and not variant.external_variant_id:
        return False
    return True


def _sellable_variants_for_product(product) -> Iterable:
    """Returns the product's sellable variants, reusing an already-prefetched `variants` cache
    (filtered in plain Python, no new query) when the caller has one — the same pattern
    `ProductSerializer._available_variants` already uses — falling back to a filtered DB query
    otherwise. Callers that need this for many products in a list view should prefetch first."""
    if hasattr(product, "_prefetched_objects_cache") and "variants" in product._prefetched_objects_cache:
        return [
            variant
            for variant in product.variants.all()
            if _is_variant_sellable(variant, mockup_template_id=product.mockup_template_id)
        ]

    queryset = product.variants.filter(is_available=True, base_cost__isnull=False)
    if product.mockup_template_id:
        queryset = queryset.filter(template_id=product.mockup_template_id)
    queryset = queryset.filter(Q(external_provider="") | ~Q(external_variant_id=""))
    return list(queryset)


def product_has_sellable_variant(product) -> bool:
    """A product is available only when it has at least one variant that belongs to it, is
    marked available, has a configured production cost, matches the product's mockup template,
    and has a valid provider mapping where one is claimed."""
    return len(_sellable_variants_for_product(product)) > 0


def sellable_variant_exists_subquery():
    """The query-level equivalent of `product_has_sellable_variant()`, for filtering a product
    LIST efficiently (one correlated EXISTS subquery per row, evaluated in SQL) instead of
    calling the Python check per product — see `apps.shop.views.ProductListView`. Must be kept
    in sync with `_is_variant_sellable()`'s criteria."""
    from apps.generator.models import ProductVariant

    return Exists(
        ProductVariant.objects.filter(
            product=OuterRef("pk"),
            template_id=OuterRef("mockup_template_id"),
            is_available=True,
            base_cost__isnull=False,
        ).filter(Q(external_provider="") | ~Q(external_variant_id=""))
    )


def get_product_starting_price(product) -> Optional[Decimal]:
    """The lowest valid selling price among the product's sellable variants, computed the same
    way the cart pricing engine prices a unit (`apps.cart.pricing.price_item`): variant
    base_cost + the single most-specific applicable PricingRule markup for this product. Does
    NOT include print-area charges (there's no specific placement/part to charge for on a bare
    listing page — see `apps.cart.pricing.PrintAreaCharge`, which is per print-*area*, not
    something a product has a "default" set of without a customer design) and does NOT treat
    `retail_price` as an override, since the cart pricing engine itself doesn't either — see
    `ProductVariant.retail_price`'s docstring. Returns None when no valid price can be
    calculated (no sellable variant) — never a synthetic $0.00."""
    from apps.cart.pricing import resolve_markup_rule  # deferred: apps.cart imports apps.shop.models at its own top

    variants = _sellable_variants_for_product(product)
    if not variants:
        return None

    rule = resolve_markup_rule(product=product)
    prices = []
    for variant in variants:
        base_cost = variant.base_cost
        if rule is None:
            markup_amount = Decimal("0.00")
        elif rule.markup_type == rule.MarkupType.FIXED:
            markup_amount = rule.amount
        else:
            markup_amount = _round(base_cost * rule.amount / Decimal("100"))
        prices.append(_round(base_cost + markup_amount))

    return min(prices)


def validate_product_can_be_activated(product) -> None:
    """Raises ValidationError with a clear, specific message if `product` isn't ready to be
    switched to `is_active=True`. Deliberately a standalone function (not just `Product.clean()`)
    — Django Admin saves the parent product row and its inline variant formset in separate
    steps, so `Product.clean()` running during the parent's own validation would see whatever
    variants happened to be saved *before* this request, not the ones just submitted alongside
    it. Callers (the admin form, `activate_product()`) must run this AFTER any inline variants
    for this request have actually been persisted — see `apps.shop.admin.ProductAdmin`."""
    variants = product.variants.all()
    if not variants.exists():
        raise ValidationError("A product must have at least one variant before activation.")

    if not product_has_sellable_variant(product):
        raise ValidationError(
            "A product must have at least one available variant with a configured production "
            "cost, matching the product's mockup template, before activation."
        )

    if not product.mockup_template_id:
        raise ValidationError("A customizable product requires a mockup template.")


@transaction.atomic
def activate_product(product):
    """The only path that should ever flip `is_active` True -> True-and-valid. Never partially
    activates: validation runs first, and raises (rolling back the transaction, since Django
    doesn't catch exceptions from inside an atomic block) before anything is written if the
    product isn't ready."""
    validate_product_can_be_activated(product)
    if not product.is_active:
        product.is_active = True
        product.save(update_fields=["is_active", "updated_at"])
    return product


@transaction.atomic
def deactivate_product(product):
    """No readiness validation needed to deactivate — a product can always be taken off the
    storefront, regardless of its variants' state."""
    if product.is_active:
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
    return product
