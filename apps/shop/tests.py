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
