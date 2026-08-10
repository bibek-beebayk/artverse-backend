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

# Product.readiness "status" values — mirrors, in plain English, exactly the checks
# validate_product_can_be_activated() already enforces at activation time, plus a check for
# regressions that can only happen *after* activation (a variant going unavailable, a provider
# mapping breaking). See get_product_readiness() below.
READINESS_READY = "ready"
READINESS_NEEDS_ATTENTION = "needs_attention"
READINESS_INACTIVE_DRAFT = "inactive_draft"


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def variant_is_sellable(variant, *, mockup_template_id: Optional[int] = None) -> bool:
    """The single definition of "sellable" a variant must meet — the one source of truth used by
    both variant-level readiness (`apps.generator.serializers.ProductVariantSerializer.is_sellable`)
    and product-level availability (`_sellable_variants_for_product`/`product_has_sellable_variant`/
    `get_product_starting_price` below), so the two can never disagree. Also mirrored (not
    literally shared, since one runs on already-loaded Python objects and the other as a
    correlated SQL subquery) by `sellable_variant_exists_subquery()` below — keep the two in
    sync if this changes.

    `is_available` reflects provider/catalogue availability (can Printify/the admin supply it at
    all); `is_sellable` additionally requires Artverse's own commercial readiness — a configured
    production cost, a template match, and a valid provider mapping where one is claimed.

    Pass `mockup_template_id` when the caller already has the parent product loaded (avoids an
    extra query per variant in a list — see callers below and
    `apps.shop.serializers.ProductSerializer.get_variants`). Omitted, it's read off
    `variant.product.mockup_template_id` (costs a query unless the caller's queryset already
    `select_related("product")`, e.g. `apps.generator.views.ProductVariantListView`)."""
    if mockup_template_id is None and variant.product_id:
        mockup_template_id = variant.product.mockup_template_id
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
            if variant_is_sellable(variant, mockup_template_id=product.mockup_template_id)
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


def get_product_readiness(product, variants: Optional[Iterable] = None) -> dict:
    """The single source of truth for the admin panel's "Ready / Needs Attention / Inactive
    Draft" status and the human-readable issue list behind it — reused by AdminProductSerializer
    (per-row, for the product list/detail) and by the admin dashboard's "Products Needing
    Attention" count. Deliberately Python, not reimplemented in React: the frontend only ever
    displays what this returns.

    `status` is derived from `is_active` + whether any issue was found, not a stored flag:
    - inactive_draft: is_active=False, regardless of issues (a draft is expected to be incomplete)
    - ready: is_active=True and no issues
    - needs_attention: is_active=True but at least one issue — this should be rare (activation
      itself is gated by validate_product_can_be_activated()) and normally means something
      regressed after activation: a variant went unavailable, a blueprint remap left the
      selected provider stale, etc.

    Pass `variants` when the caller already has `product.variants.all()` loaded/prefetched
    (e.g. a list view that prefetched `variants`) to avoid a query per product; `variants.all()`
    on an already-prefetched relation is free, but `.filter()`/`.count()` on it is NOT — this
    function only ever iterates the given/fetched list in Python."""
    if variants is None:
        variants = list(product.variants.all())
    else:
        variants = list(variants)

    issues: list[str] = []

    if not variants:
        issues.append("No variants configured.")
    else:
        missing_cost = [v for v in variants if v.base_cost is None]
        if missing_cost:
            issues.append(f"{len(missing_cost)} variant(s) missing production cost.")

        sellable = [
            v for v in variants if variant_is_sellable(v, mockup_template_id=product.mockup_template_id)
        ]
        if not sellable:
            issues.append("No sellable variants — none are available, priced, and correctly mapped.")

        if product.mockup_template_id:
            mismatched = [v for v in variants if v.template_id != product.mockup_template_id]
            if mismatched:
                issues.append(f"{len(mismatched)} variant(s) use a different template than this product.")

        incomplete_mapping = [v for v in variants if v.external_provider and not v.external_variant_id]
        if incomplete_mapping:
            issues.append(
                f"{len(incomplete_mapping)} variant(s) have an incomplete Printify mapping (missing external ID)."
            )

    if not product.mockup_template_id:
        issues.append("No mockup template selected.")
    else:
        uses_printify = any(v.external_provider for v in variants)
        # mockup_template.selected_print_provider_id: assumes the caller's queryset
        # select_related("mockup_template__selected_print_provider") when this is called at
        # list scale — see AdminProductListCreateView.
        if uses_printify and not product.mockup_template.selected_print_provider_id:
            issues.append("No Printify print provider selected for the mapped mockup template.")

    is_ready = not issues
    if not product.is_active:
        status = READINESS_INACTIVE_DRAFT
    elif is_ready:
        status = READINESS_READY
    else:
        status = READINESS_NEEDS_ATTENTION

    return {"is_ready": is_ready, "status": status, "issues": issues}


def product_readiness_issue_filter() -> Q:
    """SQL-level equivalent of "get_product_readiness(product).issues is non-empty" — for
    filtering/counting products by readiness at list scale (the admin Products filter, the
    dashboard's "Needs Attention" count) without loading every product's variants into Python.
    Must stay in sync with get_product_readiness()'s checks above; a Product queryset must be
    annotated with the boolean subqueries this Q references before it's usable — see
    apps.shop.views.annotate_product_readiness()."""
    return (
        Q(_no_variants=True)
        | Q(_missing_cost=True)
        | Q(_no_sellable_variant=True)
        | Q(_mismatched_template=True)
        | Q(_incomplete_mapping=True)
        | Q(mockup_template__isnull=True)
        | (Q(_uses_printify=True) & Q(mockup_template__selected_print_provider__isnull=True))
    )


def annotate_product_readiness(queryset):
    """Attaches the boolean subquery annotations product_readiness_issue_filter() combines into
    one "has at least one readiness issue" condition — one correlated EXISTS per check, evaluated
    in SQL per row, not a per-product Python loop. Callers still use get_product_readiness() for
    the human-readable issue text on the rows actually returned (bounded by page size); this is
    only for filtering/counting at full-catalogue scale."""
    from apps.generator.models import ProductVariant

    variants_qs = ProductVariant.objects.filter(product=OuterRef("pk"))
    return queryset.annotate(
        _no_variants=~Exists(variants_qs),
        _missing_cost=Exists(variants_qs.filter(base_cost__isnull=True)),
        _no_sellable_variant=~sellable_variant_exists_subquery(),
        _mismatched_template=Exists(variants_qs.exclude(template_id=OuterRef("mockup_template_id"))),
        _incomplete_mapping=Exists(variants_qs.filter(~Q(external_provider="") & Q(external_variant_id=""))),
        _uses_printify=Exists(variants_qs.filter(~Q(external_provider=""))),
    )


def sellable_variant_exists_subquery():
    """The query-level equivalent of `product_has_sellable_variant()`, for filtering a product
    LIST efficiently (one correlated EXISTS subquery per row, evaluated in SQL) instead of
    calling the Python check per product — see `apps.shop.views.ProductListView`. Must be kept
    in sync with `variant_is_sellable()`'s criteria."""
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
