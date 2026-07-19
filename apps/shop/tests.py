from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.generator.models import MockupTemplate, MockupTemplatePart, ProductVariant

from .models import Product, ProductCategory
from .serializers import ProductSerializer
from .services import (
    activate_product,
    deactivate_product,
    get_product_starting_price,
    product_has_sellable_variant,
    validate_product_can_be_activated,
    variant_is_sellable,
)


def make_user(username="tester"):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="testpass123")


def make_category(slug="cat-test"):
    return ProductCategory.objects.create(name=f"Category {slug}", slug=slug)


def make_template(slug="shop-test-template", **overrides):
    template = MockupTemplate.objects.create(
        name="Test Tshirt",
        slug=slug,
        product_type=MockupTemplate.ProductType.TSHIRT,
        is_active=True,
        config={"placement": {"x": 100, "y": 100, "width": 200, "height": 200, "fit": "contain"}},
        **overrides,
    )
    MockupTemplatePart.objects.create(
        template=template, name=MockupTemplatePart.PartName.FRONT, config={"placement": {"x": 10, "y": 10, "width": 50, "height": 50}}
    )
    return template


def make_product(template=None, category=None, slug="shop-test-product", is_active=False):
    category = category or make_category(slug=f"cat-{slug}")
    return Product.objects.create(
        name="Test Product", slug=slug, category=category, mockup_template=template, is_active=is_active
    )


def make_variant(product, template, *, base_cost=None, is_available=True, color_name="Black", size="M", **overrides):
    return ProductVariant.objects.create(
        product=product,
        template=template,
        color_name=color_name,
        size=size,
        base_cost=base_cost,
        is_available=is_available,
        **overrides,
    )


class ProductModelTests(TestCase):
    def setUp(self):
        self.template = make_template()

    def test_draft_product_may_have_zero_variants(self):
        product = make_product(self.template)
        self.assertEqual(product.variants.count(), 0)
        # Creating/saving a draft product with no variants must not raise anything on its own.
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_product_has_no_price_field(self):
        self.assertFalse(hasattr(Product(), "price"))

    def test_product_has_no_inventory_field(self):
        self.assertFalse(hasattr(Product(), "inventory"))

    def test_derived_availability_true_with_sellable_variant(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        self.assertTrue(product_has_sellable_variant(product))

    def test_derived_availability_false_with_no_variants(self):
        product = make_product(self.template)
        self.assertFalse(product_has_sellable_variant(product))

    def test_starting_price_uses_valid_variants(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        make_variant(product, self.template, base_cost=Decimal("8.00"), color_name="White", size="M")
        self.assertEqual(get_product_starting_price(product), Decimal("8.00"))

    def test_invalid_variants_excluded_from_starting_price(self):
        product = make_product(self.template)
        other_template = make_template(slug="other-template")
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        # Wrong template for this product — excluded even though it belongs to the product.
        make_variant(product, other_template, base_cost=Decimal("1.00"), color_name="Green", size="S")
        self.assertEqual(get_product_starting_price(product), Decimal("10.00"))

    def test_missing_cost_variants_excluded_from_starting_price(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=None, color_name="Black", size="M")
        self.assertIsNone(get_product_starting_price(product))

    def test_unavailable_variants_excluded_from_starting_price(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("5.00"), is_available=False)
        self.assertIsNone(get_product_starting_price(product))

    def test_no_valid_variants_returns_none(self):
        product = make_product(self.template)
        self.assertIsNone(get_product_starting_price(product))

    def test_starting_price_is_decimal_not_float(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        price = get_product_starting_price(product)
        self.assertIsInstance(price, Decimal)


class ProductVariantModelTests(TestCase):
    def setUp(self):
        self.template = make_template()
        self.product = make_product(self.template)

    def test_product_is_required(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductVariant.objects.create(template=self.template, color_name="Black", size="M", product=None)

    def test_orphan_variant_creation_fails(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductVariant.objects.create(template=self.template, color_name="Black", size="M", product_id=None)

    def test_variant_deletion_cascades_with_product(self):
        variant = make_variant(self.product, self.template)
        self.product.delete()
        self.assertFalse(ProductVariant.objects.filter(pk=variant.pk).exists())

    def test_template_mismatch_validation_fails(self):
        other_template = make_template(slug="mismatch-template")
        variant = ProductVariant(product=self.product, template=other_template, color_name="Black", size="M")
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_external_provider_variant_uniqueness(self):
        ProductVariant.objects.create(
            product=self.product, template=self.template, color_name="Black", size="M",
            external_provider="Printify", external_variant_id="abc123",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductVariant.objects.create(
                    product=self.product, template=self.template, color_name="White", size="L",
                    external_provider="Printify", external_variant_id="abc123",
                )

    def test_blank_external_variant_id_does_not_collide(self):
        ProductVariant.objects.create(product=self.product, template=self.template, color_name="Black", size="M")
        # Two rows both with an empty external_variant_id must not conflict — the uniqueness
        # constraint explicitly excludes the blank case.
        ProductVariant.objects.create(product=self.product, template=self.template, color_name="White", size="L")
        self.assertEqual(ProductVariant.objects.filter(product=self.product).count(), 2)

    def test_product_colour_size_uniqueness(self):
        make_variant(self.product, self.template, color_name="Black", size="M")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_variant(self.product, self.template, color_name="Black", size="M")

    def test_inventory_null_with_available_true_is_valid(self):
        variant = make_variant(self.product, self.template, is_available=True)
        variant.inventory = None
        variant.full_clean()  # must not raise — null inventory + available is a valid POD state
        variant.save()
        self.assertIsNone(variant.inventory)


class VariantSellabilityTests(TestCase):
    """Direct tests of variant_is_sellable() — the single source of truth reused by both the
    variant-level `is_sellable` API field and every product-level availability/pricing
    calculation. See apps.shop.services.variant_is_sellable's docstring."""

    def setUp(self):
        self.template = make_template()
        self.product = make_product(self.template)

    def test_available_priced_variant_is_sellable(self):
        variant = make_variant(self.product, self.template, base_cost=Decimal("10.00"), is_available=True)
        self.assertTrue(variant_is_sellable(variant))

    def test_missing_base_cost_is_not_sellable(self):
        variant = make_variant(self.product, self.template, base_cost=None, is_available=True)
        self.assertFalse(variant_is_sellable(variant))

    def test_unavailable_variant_is_not_sellable(self):
        variant = make_variant(self.product, self.template, base_cost=Decimal("10.00"), is_available=False)
        self.assertFalse(variant_is_sellable(variant))

    def test_template_mismatch_is_not_sellable(self):
        other_template = make_template(slug="mismatch-sellability")
        variant = make_variant(self.product, other_template, base_cost=Decimal("10.00"), is_available=True)
        # Explicit mockup_template_id (as apps.shop.serializers passes when it already has the
        # product loaded) catches the mismatch even though `variant.product.mockup_template_id`
        # would resolve to the same thing via the fallback path.
        self.assertFalse(variant_is_sellable(variant, mockup_template_id=self.template.id))
        # Falls back to the product's own mockup_template_id when none is passed explicitly.
        self.assertFalse(variant_is_sellable(variant))

    def test_invalid_external_mapping_is_not_sellable(self):
        variant = make_variant(
            self.product,
            self.template,
            base_cost=Decimal("10.00"),
            is_available=True,
            external_provider="Printify",
            external_variant_id="",
        )
        self.assertFalse(variant_is_sellable(variant))

    def test_valid_external_mapping_is_sellable(self):
        variant = make_variant(
            self.product,
            self.template,
            base_cost=Decimal("10.00"),
            is_available=True,
            external_provider="Printify",
            external_variant_id="12345",
        )
        self.assertTrue(variant_is_sellable(variant))

    def test_local_variant_with_no_external_provider_follows_local_rules(self):
        # A purely local/manual variant (no external_provider claimed at all) has nothing to
        # validate on the provider-mapping front — available + priced is sufficient.
        variant = make_variant(
            self.product, self.template, base_cost=Decimal("10.00"), is_available=True, external_provider=""
        )
        self.assertTrue(variant_is_sellable(variant))

    def test_mockup_template_id_fallback_reads_variant_product(self):
        # Omitting mockup_template_id falls back to variant.product.mockup_template_id — proves
        # the standalone (no-context) call path used by e.g. ProductVariantListView works.
        variant = make_variant(self.product, self.template, base_cost=Decimal("10.00"), is_available=True)
        variant = ProductVariant.objects.select_related("product").get(pk=variant.pk)
        self.assertTrue(variant_is_sellable(variant))


class ProductActivationTests(TestCase):
    def setUp(self):
        self.template = make_template()

    def test_zero_variants_cannot_activate(self):
        product = make_product(self.template)
        with self.assertRaises(ValidationError):
            validate_product_can_be_activated(product)
        with self.assertRaises(ValidationError):
            activate_product(product)
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_only_unavailable_variants_cannot_activate(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"), is_available=False)
        with self.assertRaises(ValidationError):
            activate_product(product)

    def test_only_missing_cost_variants_cannot_activate(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=None, is_available=True)
        with self.assertRaises(ValidationError):
            activate_product(product)

    def test_without_mockup_template_cannot_activate(self):
        product = make_product(template=None)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        with self.assertRaises(ValidationError):
            activate_product(product)

    def test_with_sellable_variant_activates(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        activate_product(product)
        product.refresh_from_db()
        self.assertTrue(product.is_active)

    def test_activation_does_not_partially_apply_on_failure(self):
        product = make_product(self.template)
        try:
            activate_product(product)
        except ValidationError:
            pass
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_invalid_additional_variant_does_not_block_activation_if_one_valid_exists(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        make_variant(product, self.template, base_cost=None, color_name="White", size="L")  # invalid, but not the only one
        activate_product(product)
        product.refresh_from_db()
        self.assertTrue(product.is_active)

    def test_deactivate_product(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        activate_product(product)
        deactivate_product(product)
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_activate_is_idempotent(self):
        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        activate_product(product)
        activate_product(product)  # must not raise on an already-active, still-valid product
        product.refresh_from_db()
        self.assertTrue(product.is_active)


class ProductAdminActivationTests(TestCase):
    """Confirms the admin activation path actually goes through the service, not a bare
    `is_active = True; save()`."""

    def setUp(self):
        self.template = make_template()
        self.staff = User.objects.create_superuser(username="admin", email="admin@example.com", password="pass12345")

    def _admin_request(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        request = RequestFactory().post("/admin/shop/product/")
        request.user = self.staff
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def test_admin_activate_action_uses_the_service(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import ProductAdmin

        product = make_product(self.template)
        make_variant(product, self.template, base_cost=Decimal("10.00"))

        admin_instance = ProductAdmin(Product, AdminSite())
        admin_instance.activate_selected(self._admin_request(), Product.objects.filter(pk=product.pk))
        product.refresh_from_db()
        self.assertTrue(product.is_active)

    def test_admin_bulk_activation_reports_failures_without_raising(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import ProductAdmin

        good = make_product(self.template, slug="good-product")
        make_variant(good, self.template, base_cost=Decimal("10.00"))
        bad = make_product(self.template, slug="bad-product")  # zero variants

        admin_instance = ProductAdmin(Product, AdminSite())

        # Must not raise even though `bad` fails validation.
        admin_instance.activate_selected(self._admin_request(), Product.objects.filter(pk__in=[good.pk, bad.pk]))
        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertTrue(good.is_active)
        self.assertFalse(bad.is_active)


class ProductPublicVisibilityTests(APITestCase):
    def setUp(self):
        self.template = make_template()

    def test_inactive_product_is_hidden(self):
        product = make_product(self.template, is_active=False)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        list_response = self.client.get("/api/shop/products/")
        self.assertNotIn(product.id, [row["id"] for row in list_response.data])

    def test_active_product_without_sellable_variant_is_hidden(self):
        product = make_product(self.template, is_active=True)
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_active_product_with_sellable_variant_is_returned(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_starting_price_is_serialized(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.data["starting_price"], "10.00")

    def test_price_and_inventory_fields_absent(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertNotIn("price", response.data)
        self.assertNotIn("inventory", response.data)

    def test_available_variant_count_is_correct(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        make_variant(product, self.template, base_cost=None, color_name="White", size="L")  # not sellable
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.data["available_variant_count"], 1)
        self.assertEqual(response.data["total_variant_count"], 2)

    def test_product_detail_rejects_unavailable_product_publicly(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), is_available=False)
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_endpoint_only_returns_sellable_active_products(self):
        visible = make_product(self.template, is_active=True, slug="visible")
        make_variant(visible, self.template, base_cost=Decimal("10.00"))
        make_product(self.template, is_active=False, slug="hidden-inactive")
        make_product(self.template, is_active=True, slug="hidden-no-variant")

        response = self.client.get("/api/shop/products/")
        slugs = [row["slug"] for row in response.data]
        self.assertEqual(slugs, ["visible"])


class ProductSerializerDirectTests(TestCase):
    """Exercises ProductSerializer without going through a queryset's Exists filter — verifies
    the derived fields themselves, independent of public-visibility filtering."""

    def setUp(self):
        self.template = make_template()

    def test_is_available_and_counts_without_prefetch(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        data = ProductSerializer(product).data
        self.assertTrue(data["is_available"])
        self.assertEqual(data["available_variant_count"], 1)
        self.assertEqual(data["total_variant_count"], 1)

    def test_starting_price_none_when_unavailable(self):
        product = make_product(self.template, is_active=True)
        data = ProductSerializer(product).data
        self.assertIsNone(data["starting_price"])
        self.assertFalse(data["is_available"])


class ProductVariantSellabilityConsistencyTests(APITestCase):
    """Section 18/19: Product.is_available / available_variant_count / starting_price and each
    nested variant's is_sellable must never contradict each other — all four are derived from
    the exact same `variant_is_sellable()` source of truth."""

    def setUp(self):
        self.template = make_template()

    def test_one_sellable_variant_is_internally_consistent(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertTrue(response.data["is_available"])
        self.assertEqual(response.data["available_variant_count"], 1)
        self.assertIsNotNone(response.data["starting_price"])
        sellable_flags = [v["is_sellable"] for v in response.data["variants"]]
        self.assertEqual(sellable_flags.count(True), response.data["available_variant_count"])

    def test_all_variants_missing_cost_matches_product_level_unavailability(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=None, color_name="Black", size="M")
        make_variant(product, self.template, base_cost=None, color_name="White", size="L")
        # A product with only missing-cost variants has no sellable variant at all, so it's not
        # publicly reachable — this itself is the consistency guarantee (never "is_available: true
        # with available_variant_count: 0" or similar contradiction).
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # Verified directly via the serializer (bypassing the public-visibility filter) too.
        data = ProductSerializer(product).data
        self.assertFalse(data["is_available"])
        self.assertEqual(data["available_variant_count"], 0)
        self.assertIsNone(data["starting_price"])
        self.assertTrue(all(v["is_sellable"] is False for v in data["variants"]))

    def test_serializer_variant_readiness_matches_product_calculation(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), color_name="Black", size="M")
        make_variant(product, self.template, base_cost=None, color_name="White", size="L")
        make_variant(product, self.template, base_cost=Decimal("5.00"), is_available=False, color_name="Red", size="S")
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        by_color = {v["color_name"]: v for v in response.data["variants"]}
        self.assertTrue(by_color["Black"]["is_sellable"])
        self.assertFalse(by_color["White"]["is_sellable"])  # missing cost
        self.assertFalse(by_color["Red"]["is_sellable"])  # unavailable
        # Product-level available_variant_count must equal the number of variants the nested
        # list itself marks is_sellable=True — no separate, potentially-drifting calculation.
        sellable_count_from_variants = sum(1 for v in response.data["variants"] if v["is_sellable"])
        self.assertEqual(response.data["available_variant_count"], sellable_count_from_variants)


class ProductVariantSerializerFieldTests(APITestCase):
    """Section 19 'Serializer' checklist: product_id numeric, is_sellable/pricing_ready present,
    missing-cost variants clearly marked unsellable, product price/inventory absent."""

    def setUp(self):
        self.template = make_template()

    def test_product_id_is_always_numeric(self):
        product = make_product(self.template, is_active=True)
        variant = make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/generator/product-variants/?product_id={product.id}")
        row = next(v for v in response.data if v["id"] == variant.id)
        self.assertIsInstance(row["product_id"], int)
        self.assertEqual(row["product_id"], product.id)

    def test_is_sellable_and_pricing_ready_present_on_standalone_endpoint(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=None, color_name="Black", size="M")
        response = self.client.get(f"/api/generator/product-variants/?product_id={product.id}")
        row = response.data[0]
        self.assertIn("is_sellable", row)
        self.assertIn("pricing_ready", row)
        self.assertFalse(row["pricing_ready"])
        self.assertFalse(row["is_sellable"])

    def test_unavailable_variants_are_included_not_filtered(self):
        # ProductVariantListView deliberately no longer filters to is_available=True — the
        # frontend needs unavailable rows too, to render disabled options.
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"), is_available=False)
        response = self.client.get(f"/api/generator/product-variants/?product_id={product.id}")
        self.assertEqual(len(response.data), 1)
        self.assertFalse(response.data[0]["is_available"])

    def test_product_price_and_inventory_remain_absent_alongside_new_fields(self):
        product = make_product(self.template, is_active=True)
        make_variant(product, self.template, base_cost=Decimal("10.00"))
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertNotIn("price", response.data)
        self.assertNotIn("inventory", response.data)
        self.assertIn("starting_price", response.data)
        variant_row = response.data["variants"][0]
        self.assertIn("is_sellable", variant_row)
        self.assertIn("pricing_ready", variant_row)
