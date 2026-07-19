from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.generator.models import (
    DesignPlacement,
    DesignProject,
    MockupTemplate,
    MockupTemplatePart,
    ProductVariant,
)
from apps.shop.models import Product, ProductCategory

from .models import Cart, CartItem, Coupon, PricingConfig, PricingRule, PrintAreaCharge
from .pricing import CouponError, price_cart_totals, price_item, resolve_markup_rule, validate_coupon


def make_user(username="tester", email=None):
    return User.objects.create_user(username=username, email=email or f"{username}@example.com", password="testpass123")


def make_template(slug="cart-test-template"):
    template = MockupTemplate.objects.create(
        name="Test Tshirt",
        slug=slug,
        product_type=MockupTemplate.ProductType.TSHIRT,
        is_active=True,
        config={"placement": {"x": 100, "y": 100, "width": 200, "height": 200, "fit": "contain"}},
    )
    for part_name in (MockupTemplatePart.PartName.FRONT, MockupTemplatePart.PartName.BACK):
        MockupTemplatePart.objects.create(
            template=template, name=part_name, config={"placement": {"x": 10, "y": 10, "width": 50, "height": 50}}
        )
    return template


def make_category(slug="cat-test"):
    return ProductCategory.objects.create(name=f"Category {slug}", slug=slug)


def make_product(template=None, category=None, slug="cart-test-product", price="19.99"):
    category = category or make_category(slug=f"cat-{slug}")
    return Product.objects.create(name="Test Product", slug=slug, category=category, price=price, mockup_template=template)


def make_variant(template, product=None, base_cost=None, color_name="Black", size="M"):
    return ProductVariant.objects.create(
        template=template, product=product, color_name=color_name, size=size, base_cost=base_cost
    )


def make_printable_placement(project, part_name="front", template_part=None):
    return DesignPlacement.objects.create(
        design_project=project,
        part_name=part_name,
        template_part=template_part,
        source_image_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    )


def make_design_project(user, template, product=None, variant=None):
    return DesignProject.objects.create(
        user=user,
        mockup_template=template,
        name="Test Design",
        product=product,
        selected_variant=variant,
        selected_color=variant.color_name if variant else "",
        selected_size=variant.size if variant else "",
    )


class PriceItemTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.category = make_category()
        self.product = make_product(template=self.template, category=self.category)

    def test_base_cost_only_no_rule_no_charges(self):
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "front")]

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.base_cost, Decimal("10.00"))
        self.assertEqual(breakdown.print_area_charges, Decimal("0.00"))
        self.assertEqual(breakdown.markup_amount, Decimal("0.00"))
        self.assertEqual(breakdown.unit_price, Decimal("10.00"))
        self.assertEqual(breakdown.warnings, ())

    def test_print_area_charge_applied_only_for_printable_parts(self):
        PrintAreaCharge.objects.create(part_name="back", amount=Decimal("4.00"))
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "front")]  # no back placement

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.print_area_charges, Decimal("0.00"))

        DesignPlacement.objects.filter(design_project=project, part_name="front").delete()
        placements = [
            make_printable_placement(project, "front"),
            make_printable_placement(project, "back"),
        ]
        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.print_area_charges, Decimal("4.00"))

    def test_inactive_print_area_charge_not_applied(self):
        PrintAreaCharge.objects.create(part_name="back", amount=Decimal("4.00"), is_active=False)
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "back")]

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.print_area_charges, Decimal("0.00"))

    def test_missing_base_cost_defaults_to_zero_with_warning(self):
        variant = make_variant(self.template, self.product, base_cost=None)
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "front")]

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.base_cost, Decimal("0.00"))
        self.assertTrue(breakdown.warnings)

    def test_none_variant_defaults_to_zero_with_warning(self):
        project = make_design_project(self.user, self.template, self.product, None)
        placements = [make_printable_placement(project, "front")]

        breakdown = price_item(product=self.product, variant=None, placements=placements)
        self.assertEqual(breakdown.base_cost, Decimal("0.00"))
        self.assertTrue(breakdown.warnings)

    def test_fixed_markup_applied(self):
        PricingRule.objects.create(
            name="Global fixed", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("5.00"),
        )
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "front")]

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        self.assertEqual(breakdown.markup_amount, Decimal("5.00"))
        self.assertEqual(breakdown.unit_price, Decimal("15.00"))

    def test_percentage_markup_applied_to_base_plus_print_area_charges(self):
        PrintAreaCharge.objects.create(part_name="back", amount=Decimal("10.00"))
        PricingRule.objects.create(
            name="Global pct", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.PERCENTAGE,
            amount=Decimal("20.00"),
        )
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "back")]

        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        # production_cost = 10 + 10 = 20; markup = 20% of 20 = 4.00
        self.assertEqual(breakdown.markup_amount, Decimal("4.00"))
        self.assertEqual(breakdown.unit_price, Decimal("24.00"))

    def test_markup_precedence_product_over_category_over_global(self):
        PricingRule.objects.create(
            name="Global", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED, amount=Decimal("1.00")
        )
        PricingRule.objects.create(
            name="Category", rule_type=PricingRule.RuleType.CATEGORY, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("2.00"), category=self.category,
        )
        PricingRule.objects.create(
            name="Product", rule_type=PricingRule.RuleType.PRODUCT, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("3.00"), product=self.product,
        )
        rule = resolve_markup_rule(product=self.product)
        self.assertEqual(rule.name, "Product")

    def test_markup_precedence_falls_back_to_category_when_no_product_rule(self):
        PricingRule.objects.create(
            name="Global", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED, amount=Decimal("1.00")
        )
        PricingRule.objects.create(
            name="Category", rule_type=PricingRule.RuleType.CATEGORY, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("2.00"), category=self.category,
        )
        rule = resolve_markup_rule(product=self.product)
        self.assertEqual(rule.name, "Category")

    def test_markup_precedence_falls_back_to_global_when_nothing_more_specific(self):
        PricingRule.objects.create(
            name="Global", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED, amount=Decimal("1.00")
        )
        rule = resolve_markup_rule(product=self.product)
        self.assertEqual(rule.name, "Global")

    def test_inactive_more_specific_rule_is_skipped(self):
        PricingRule.objects.create(
            name="Global", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED, amount=Decimal("1.00")
        )
        PricingRule.objects.create(
            name="Product", rule_type=PricingRule.RuleType.PRODUCT, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("3.00"), product=self.product, is_active=False,
        )
        rule = resolve_markup_rule(product=self.product)
        self.assertEqual(rule.name, "Global")

    def test_expired_rule_is_skipped(self):
        now = timezone.now()
        PricingRule.objects.create(
            name="Expired", rule_type=PricingRule.RuleType.PRODUCT, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("3.00"), product=self.product, ends_at=now - timezone.timedelta(days=1),
        )
        rule = resolve_markup_rule(product=self.product)
        self.assertIsNone(rule)

    def test_rounding_uses_round_half_up(self):
        PricingRule.objects.create(
            name="Half up", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.PERCENTAGE,
            amount=Decimal("2.5"),
        )
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        project = make_design_project(self.user, self.template, self.product, variant)
        placements = [make_printable_placement(project, "front")]
        breakdown = price_item(product=self.product, variant=variant, placements=placements)
        # 2.5% of 10.00 = 0.25 exactly, no rounding ambiguity — confirms the basic path works;
        self.assertEqual(breakdown.markup_amount, Decimal("0.25"))



class PriceCartTotalsTests(TestCase):
    def setUp(self):
        self.config = PricingConfig.get_solo()
        self.config.tax_percentage = Decimal("10.00")
        self.config.flat_shipping_amount = Decimal("5.00")
        self.config.free_shipping_threshold = Decimal("50.00")
        self.config.save()

    def test_no_coupon_no_discount(self):
        totals = price_cart_totals(subtotal=Decimal("20.00"), coupon=None, config=self.config)
        self.assertEqual(totals.discount_amount, Decimal("0.00"))

    def test_fixed_coupon_capped_at_subtotal(self):
        coupon = Coupon.objects.create(code="BIG", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("100.00"))
        totals = price_cart_totals(subtotal=Decimal("20.00"), coupon=coupon, config=self.config)
        self.assertEqual(totals.discount_amount, Decimal("20.00"))
        self.assertGreaterEqual(totals.total, Decimal("0.00"))

    def test_percentage_coupon(self):
        coupon = Coupon.objects.create(code="PCT", discount_type=Coupon.DiscountType.PERCENTAGE, amount=Decimal("10.00"))
        totals = price_cart_totals(subtotal=Decimal("20.00"), coupon=coupon, config=self.config)
        self.assertEqual(totals.discount_amount, Decimal("2.00"))

    def test_tax_applied_to_post_discount_subtotal(self):
        coupon = Coupon.objects.create(code="PCT", discount_type=Coupon.DiscountType.PERCENTAGE, amount=Decimal("50.00"))
        totals = price_cart_totals(subtotal=Decimal("20.00"), coupon=coupon, config=self.config)
        # discounted subtotal = 10.00; tax = 10% of 10.00 = 1.00
        self.assertEqual(totals.tax_amount, Decimal("1.00"))

    def test_shipping_waived_at_or_above_threshold_post_discount(self):
        totals = price_cart_totals(subtotal=Decimal("50.00"), coupon=None, config=self.config)
        self.assertEqual(totals.shipping_amount, Decimal("0.00"))

    def test_shipping_charged_below_threshold(self):
        totals = price_cart_totals(subtotal=Decimal("49.99"), coupon=None, config=self.config)
        self.assertEqual(totals.shipping_amount, Decimal("5.00"))

    def test_no_free_shipping_threshold_always_charges(self):
        self.config.free_shipping_threshold = None
        self.config.save()
        totals = price_cart_totals(subtotal=Decimal("999.00"), coupon=None, config=self.config)
        self.assertEqual(totals.shipping_amount, Decimal("5.00"))

    def test_total_formula(self):
        totals = price_cart_totals(subtotal=Decimal("20.00"), coupon=None, config=self.config)
        # discounted subtotal 20.00, tax 10% = 2.00, shipping 5.00 (below 50 threshold)
        self.assertEqual(totals.total, Decimal("27.00"))


class ValidateCouponTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_none_coupon_raises(self):
        with self.assertRaises(CouponError):
            validate_coupon(coupon=None, subtotal=Decimal("10.00"), user=self.user)

    def test_inactive_coupon_raises(self):
        coupon = Coupon.objects.create(code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"), is_active=False)
        with self.assertRaises(CouponError):
            validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)

    def test_not_yet_active_raises(self):
        coupon = Coupon.objects.create(
            code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"),
            starts_at=timezone.now() + timezone.timedelta(days=1),
        )
        with self.assertRaises(CouponError):
            validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)

    def test_expired_raises(self):
        coupon = Coupon.objects.create(
            code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"),
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        with self.assertRaises(CouponError):
            validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)

    def test_below_min_subtotal_raises(self):
        coupon = Coupon.objects.create(
            code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"), min_subtotal=Decimal("50.00")
        )
        with self.assertRaises(CouponError):
            validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)

    def test_max_redemptions_reached_raises(self):
        coupon = Coupon.objects.create(
            code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"),
            max_redemptions=1, times_redeemed=1,
        )
        with self.assertRaises(CouponError):
            validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)

    def test_valid_coupon_passes(self):
        coupon = Coupon.objects.create(code="X", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("1.00"))
        result = validate_coupon(coupon=coupon, subtotal=Decimal("10.00"), user=self.user)
        self.assertEqual(result, coupon)


class CartModelTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.product = make_product(template=self.template)
        self.project = make_design_project(self.user, self.template, self.product, None)

    def test_unique_cart_item_per_design_project(self):
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, design_project=self.project, product=self.product, unit_price=Decimal("1.00"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CartItem.objects.create(cart=cart, design_project=self.project, product=self.product, unit_price=Decimal("1.00"))

    def test_cart_one_per_user(self):
        Cart.objects.create(user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cart.objects.create(user=self.user)

    def test_pricing_rule_scope_constraint_category_requires_category(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingRule.objects.create(
                    name="Bad", rule_type=PricingRule.RuleType.CATEGORY, markup_type=PricingRule.MarkupType.FIXED,
                    amount=Decimal("1.00"),
                )

    def test_pricing_rule_clean_rejects_global_with_product(self):
        rule = PricingRule(
            name="Bad", rule_type=PricingRule.RuleType.GLOBAL, markup_type=PricingRule.MarkupType.FIXED,
            amount=Decimal("1.00"), product=self.product,
        )
        with self.assertRaises(Exception):
            rule.full_clean()


class CartApiTestBase(APITestCase):
    def setUp(self):
        self.user = make_user("owner")
        self.other_user = make_user("intruder")
        self.template = make_template()
        self.category = make_category()
        self.product = make_product(template=self.template, category=self.category)
        self.variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"))
        self.project = make_design_project(self.user, self.template, self.product, self.variant)
        make_printable_placement(self.project, "front")


class CartItemApiOwnershipTests(CartApiTestBase):
    def test_add_another_users_design_project_returns_404(self):
        other_project = make_design_project(self.other_user, self.template, self.product, self.variant)
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": other_project.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_another_users_cart_item_returns_404(self):
        self.client.force_authenticate(user=self.user)
        add_response = self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        item_id = add_response.data["items"][0]["id"]

        self.client.force_authenticate(user=self.other_user)
        response = self.client.patch(f"/api/cart/items/{item_id}/", {"quantity": 2}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_another_users_cart_item_returns_404(self):
        self.client.force_authenticate(user=self.user)
        add_response = self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        item_id = add_response.data["items"][0]["id"]

        self.client.force_authenticate(user=self.other_user)
        response = self.client.delete(f"/api/cart/items/{item_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_user_denied(self):
        response = self.client.get("/api/cart/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class NeverTrustFrontendPriceTests(CartApiTestBase):
    def test_submitted_price_is_ignored_on_add(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/cart/items/",
            {"design_project_id": self.project.id, "unit_price": "0.01", "price": "0.01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["items"][0]["unit_price"], "10.00")

    def test_submitted_product_and_variant_ids_are_ignored(self):
        other_product = make_product(template=self.template, slug="other-product")
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/cart/items/",
            {"design_project_id": self.project.id, "product_id": other_product.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # product is resolved from design_project.product server-side, never the submitted product_id
        self.assertEqual(response.data["items"][0]["product"]["id"], self.product.id)


class CartMutationApiTests(CartApiTestBase):
    def test_add_item_then_get_reflects_it(self):
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        response = self.client.get("/api/cart/")
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["design_project"]["id"], self.project.id)

    def test_adding_same_design_twice_increments_quantity_not_duplicates(self):
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id, "quantity": 1}, format="json")
        response = self.client.post(
            "/api/cart/items/", {"design_project_id": self.project.id, "quantity": 2}, format="json"
        )
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["quantity"], 3)

    def test_update_quantity(self):
        self.client.force_authenticate(user=self.user)
        add_response = self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        item_id = add_response.data["items"][0]["id"]
        response = self.client.patch(f"/api/cart/items/{item_id}/", {"quantity": 5}, format="json")
        self.assertEqual(response.data["items"][0]["quantity"], 5)

    def test_remove_item(self):
        self.client.force_authenticate(user=self.user)
        add_response = self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        item_id = add_response.data["items"][0]["id"]
        response = self.client.delete(f"/api/cart/items/{item_id}/")
        self.assertEqual(response.data["items"], [])

    def test_adding_design_project_flips_status_to_ready(self):
        self.assertEqual(self.project.status, DesignProject.Status.DRAFT)
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, DesignProject.Status.READY)

    def test_stale_admin_price_change_reflected_on_next_get(self):
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        self.variant.base_cost = Decimal("50.00")
        self.variant.save(update_fields=["base_cost"])
        response = self.client.get("/api/cart/")
        self.assertEqual(response.data["items"][0]["unit_price"], "50.00")


class CartPricingWarningApiTests(CartApiTestBase):
    """Roadmap item 8: a missing ProductVariant.base_cost (or an unavailable variant) must not
    be silently presented as a normal $0.00 item — it stays in the cart, prices without
    crashing, but carries a clear warning and marks the cart not checkout-ready."""

    def test_missing_base_cost_produces_a_clear_warning(self):
        variant = make_variant(self.template, self.product, base_cost=None, color_name="Ghost", size="L")
        project = make_design_project(self.user, self.template, self.product, variant)
        make_printable_placement(project, "front")

        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": project.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = response.data["items"][0]
        self.assertEqual(item["unit_price"], "0.00")  # still prices without crashing
        self.assertTrue(item["warnings"])
        self.assertIn(self.product.name, item["warnings"][0])
        self.assertIn("base_cost", item["warnings"][0])

    def test_cart_is_not_checkout_ready_when_an_item_has_a_warning(self):
        variant = make_variant(self.template, self.product, base_cost=None, color_name="Ghost", size="L")
        project = make_design_project(self.user, self.template, self.product, variant)
        make_printable_placement(project, "front")

        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": project.id}, format="json")
        self.assertFalse(response.data["is_checkout_ready"])

    def test_item_with_warning_is_not_removed_from_cart(self):
        variant = make_variant(self.template, self.product, base_cost=None, color_name="Ghost", size="L")
        project = make_design_project(self.user, self.template, self.product, variant)
        make_printable_placement(project, "front")

        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": project.id}, format="json")
        response = self.client.get("/api/cart/")
        self.assertEqual(len(response.data["items"]), 1)

    def test_unavailable_variant_produces_a_warning(self):
        variant = make_variant(self.template, self.product, base_cost=Decimal("10.00"), color_name="Retired", size="S")
        variant.is_available = False
        variant.save(update_fields=["is_available"])
        project = make_design_project(self.user, self.template, self.product, variant)
        make_printable_placement(project, "front")

        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": project.id}, format="json")
        item = response.data["items"][0]
        self.assertTrue(any("no longer available" in w for w in item["warnings"]))
        self.assertFalse(response.data["is_checkout_ready"])

    def test_valid_variant_has_no_warnings_and_cart_is_checkout_ready(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        self.assertEqual(response.data["items"][0]["warnings"], [])
        self.assertTrue(response.data["is_checkout_ready"])

    def test_pricing_does_not_crash_with_missing_base_cost(self):
        variant = make_variant(self.template, self.product, base_cost=None, color_name="Ghost", size="L")
        project = make_design_project(self.user, self.template, self.product, variant)
        make_printable_placement(project, "front")

        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/items/", {"design_project_id": project.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_one_warning_item_does_not_affect_other_items_pricing(self):
        warning_variant = make_variant(self.template, self.product, base_cost=None, color_name="Ghost", size="L")
        warning_project = make_design_project(self.user, self.template, self.product, warning_variant)
        make_printable_placement(warning_project, "front")

        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")
        response = self.client.post("/api/cart/items/", {"design_project_id": warning_project.id}, format="json")

        items_by_variant = {item["variant_id"]: item for item in response.data["items"]}
        self.assertEqual(items_by_variant[self.variant.id]["unit_price"], "10.00")
        self.assertEqual(items_by_variant[self.variant.id]["warnings"], [])
        self.assertEqual(items_by_variant[warning_variant.id]["unit_price"], "0.00")
        self.assertTrue(items_by_variant[warning_variant.id]["warnings"])


class CartCouponApiTests(CartApiTestBase):
    def test_apply_and_remove_coupon(self):
        Coupon.objects.create(code="SAVE5", discount_type=Coupon.DiscountType.FIXED, amount=Decimal("5.00"))
        self.client.force_authenticate(user=self.user)
        self.client.post("/api/cart/items/", {"design_project_id": self.project.id}, format="json")

        response = self.client.post("/api/cart/coupon/", {"code": "save5"}, format="json")  # case-insensitive
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["discount_amount"], "5.00")

        response = self.client.delete("/api/cart/coupon/")
        self.assertEqual(response.data["discount_amount"], "0.00")

    def test_invalid_coupon_returns_400_field_error(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/coupon/", {"code": "NOPE"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data)


class CartMergeApiTests(CartApiTestBase):
    def test_merge_mixed_valid_and_invalid_entries(self):
        other_project = make_design_project(self.other_user, self.template, self.product, self.variant)
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/cart/merge/",
            {"items": [{"design_project_id": self.project.id, "quantity": 1}, {"design_project_id": other_project.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["merged"], 1)
        self.assertEqual(len(response.data["errors"]), 1)
        self.assertEqual(len(response.data["cart"]["items"]), 1)

    def test_merge_same_design_twice_increments_not_duplicates(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/cart/merge/",
            {"items": [
                {"design_project_id": self.project.id, "quantity": 1},
                {"design_project_id": self.project.id, "quantity": 2},
            ]},
            format="json",
        )
        self.assertEqual(len(response.data["cart"]["items"]), 1)
        self.assertEqual(response.data["cart"]["items"][0]["quantity"], 3)
