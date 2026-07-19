from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PricingConfig(models.Model):
    """Global pricing knobs — singleton, mirrors apps.accounts.models.SiteConfiguration's
    get_solo() pattern. Tax and shipping here are deliberately simple flat-rate placeholders,
    not real tax-jurisdiction or shipping-rate-shopping calculation — see apps/cart/pricing.py's
    module docstring for why, and TODO.md item 31 for where real shipping-rate calculation
    belongs (a separate, later task)."""

    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text="Main selling currency (ISO 4217). Multi-currency conversion is future work — see TODO.md item 29.",
    )
    tax_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Flat percentage applied to the post-discount subtotal. Placeholder only — no tax-jurisdiction calculation.",
    )
    flat_shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("5.99"))
    free_shipping_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Post-discount subtotal at/above which shipping is waived. Blank means no free-shipping offer.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pricing Configuration"
        verbose_name_plural = "Pricing Configuration"

    def __str__(self) -> str:
        return "Global Pricing Configuration"

    @classmethod
    def get_solo(cls) -> "PricingConfig":
        config, _ = cls.objects.get_or_create(pk=1)
        return config


class PrintAreaCharge(models.Model):
    """Extra per-unit cost for printing on a given template part (e.g. back/sleeves cost more
    than front). Unifies the roadmap's "printing-area charges" and "additional sleeve/back
    charges" into one mechanism — there's no other data anywhere distinguishing the two. A part
    with no row here, or an inactive row, is treated as 0.00, so admins only configure the parts
    that actually cost extra. `part_name` intentionally isn't a FK to MockupTemplatePart — this
    charge is per part *name* (front/back/sleeve) globally, not per specific template part row,
    since the extra cost for "printing on the back" doesn't vary by which shirt it is."""

    part_name = models.CharField(max_length=30, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("part_name",)

    def __str__(self) -> str:
        return f"{self.part_name}: {self.amount}"


class PricingRule(models.Model):
    """Admin-configurable markup. Resolution is by SPECIFICITY, not stacking — see
    pricing.resolve_markup_rule(): only the single most-specific active, in-window rule applies
    (product-specific > category > global). Rules are never compounded on the same item, since
    stacking a global + category + product markup would silently compound margins in a way no
    admin UI would make obvious."""

    class RuleType(models.TextChoices):
        GLOBAL = "global", "Global (all products)"
        CATEGORY = "category", "Category"
        PRODUCT = "product", "Product-specific"

    class MarkupType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENTAGE = "percentage", "Percentage"

    name = models.CharField(max_length=255)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    markup_type = models.CharField(max_length=20, choices=MarkupType.choices)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Flat currency amount if markup_type=fixed, or percentage points (15.00 = 15%) if markup_type=percentage.",
    )
    category = models.ForeignKey(
        "shop.ProductCategory", on_delete=models.CASCADE, null=True, blank=True, related_name="pricing_rules"
    )
    product = models.ForeignKey(
        "shop.Product", on_delete=models.CASCADE, null=True, blank=True, related_name="pricing_rules"
    )
    currency = models.CharField(max_length=3, default="USD")
    priority = models.PositiveIntegerField(
        default=0, help_text="Tie-breaker when more than one active rule of the same rule_type matches; higher wins."
    )
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("rule_type", "-priority", "id")
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(rule_type="global", category__isnull=True, product__isnull=True)
                    | models.Q(rule_type="category", category__isnull=False, product__isnull=True)
                    | models.Q(rule_type="product", category__isnull=True, product__isnull=False)
                ),
                name="pricingrule_scope_matches_rule_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.rule_type}/{self.markup_type})"

    def clean(self):
        super().clean()
        if self.rule_type == self.RuleType.CATEGORY and not self.category_id:
            raise ValidationError("Category markup rules require a category.")
        if self.rule_type == self.RuleType.PRODUCT and not self.product_id:
            raise ValidationError("Product-specific markup rules require a product.")
        if self.rule_type == self.RuleType.GLOBAL and (self.category_id or self.product_id):
            raise ValidationError("Global markup rules must not set a category or product.")


class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed amount off"
        PERCENTAGE = "percentage", "Percentage off"

    code = models.CharField(max_length=32, unique=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    min_subtotal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_redemptions = models.PositiveIntegerField(
        null=True, blank=True, help_text="Total uses across all customers. Blank means unlimited."
    )
    max_redemptions_per_user = models.PositiveIntegerField(null=True, blank=True, default=1)
    times_redeemed = models.PositiveIntegerField(
        default=0,
        help_text="Incremented on completed ORDERS only (Section 5, not built yet) — applying a "
        "coupon to a cart never increments this.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.code


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="carts")
    currency = models.CharField(max_length=3, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Cart #{self.pk} ({self.user})"


class CartItem(models.Model):
    """One design in one user's cart. Print placements/crop/text are read from
    `design_project.placements`, never duplicated here — DesignPlacement already owns that data
    (see apps.generator.models). `unit_price` is always server-computed by
    apps.cart.pricing.price_item() and never accepted from a client request."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    design_project = models.ForeignKey(
        "generator.DesignProject", on_delete=models.CASCADE, related_name="cart_items"
    )
    product = models.ForeignKey("shop.Product", on_delete=models.PROTECT, related_name="cart_items")
    variant = models.ForeignKey(
        "generator.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="cart_items"
    )
    size = models.CharField(max_length=120, blank=True)
    colour = models.CharField(max_length=120, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    preview_image_url = models.TextField(blank=True)
    pricing_breakdown = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of the last pricing computation (base cost, print-area charges, "
        "markup, warnings) for support/debugging and future checkout re-display.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(fields=["cart", "design_project"], name="unique_cart_item_per_design_project"),
        ]

    def __str__(self) -> str:
        return f"CartItem #{self.pk} ({self.design_project})"
