from io import StringIO
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.generator.models import MockupTemplate, MockupTemplatePart, ProductVariant
from apps.shop.models import Product, ProductCategory

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun
from .services import (
    PrintifyAPIError,
    PrintifyClient,
    PrintifyNotConfiguredError,
    PrintifyResponseError,
    PrintifyShopNotFoundError,
    fetch_print_provider_location,
    fetch_print_provider_variants,
    fetch_shops,
    sync_blueprints,
    sync_print_providers_for_blueprint,
    sync_product_variants_from_printify,
    validate_configured_shop,
)
from .validation import (
    get_provider_placeholder_positions,
    validate_placeholder_position,
    validate_provider_matches_template_blueprint,
)


def make_template(slug="tshirt-test"):
    template = MockupTemplate.objects.create(
        name="Test Tshirt", slug=slug, product_type=MockupTemplate.ProductType.TSHIRT, is_active=True
    )
    MockupTemplatePart.objects.create(template=template, name=MockupTemplatePart.PartName.FRONT)
    return template


def make_shop_product(template, slug="test-product"):
    category = ProductCategory.objects.create(name=f"Cat {slug}", slug=f"cat-{slug}")
    return Product.objects.create(
        name="Test Product", slug=slug, category=category, mockup_template=template, is_active=True
    )


def make_user(username="tester", email=None, is_staff=False):
    return User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="testpass123", is_staff=is_staff
    )


def mock_response(status_code=200, json_data=None, headers=None):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.headers = headers or {}
    response.content = b"{}" if json_data is not None else b""
    response.json.return_value = json_data if json_data is not None else {}
    response.text = str(json_data)
    return response


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class PrintifyClientTests(TestCase):
    def test_missing_token_raises_not_configured(self):
        with self.assertRaises(PrintifyNotConfiguredError):
            PrintifyClient(api_token="")

    @patch("requests.Session.request")
    def test_get_returns_json_on_success(self, mock_request):
        mock_request.return_value = mock_response(200, {"hello": "world"})
        client = PrintifyClient()
        self.assertEqual(client.get("/catalog/blueprints.json"), {"hello": "world"})

    @patch("requests.Session.request")
    def test_get_raises_api_error_on_4xx(self, mock_request):
        mock_request.return_value = mock_response(404, {"error": "not found"})
        client = PrintifyClient()
        with self.assertRaises(PrintifyAPIError) as ctx:
            client.get("/catalog/blueprints/999.json")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("time.sleep", return_value=None)
    @patch("requests.Session.request")
    def test_retries_on_429_then_succeeds(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            mock_response(429, headers={"Retry-After": "1"}),
            mock_response(200, {"ok": True}),
        ]
        client = PrintifyClient()
        self.assertEqual(client.get("/catalog/blueprints.json"), {"ok": True})
        self.assertEqual(mock_request.call_count, 2)

    @patch("time.sleep", return_value=None)
    @patch("requests.Session.request")
    def test_retries_exhausted_raises(self, mock_request, mock_sleep):
        mock_request.return_value = mock_response(500, {"error": "server error"})
        client = PrintifyClient()
        with self.assertRaises(PrintifyAPIError):
            client.get("/catalog/blueprints.json")
        self.assertEqual(mock_request.call_count, 3)


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class FetchHelpersTests(TestCase):
    """Printify's real catalogue API has no boolean "in stock" field on a variant — availability
    is expressed purely by whether the variant is present at all when `show-out-of-stock` is
    omitted (confirmed against the live API). fetch_print_provider_variants() makes two requests
    and folds that into a synthetic `is_enabled` field. Also: provider location isn't in the
    blueprint-scoped print_providers list endpoint — it's only on the standalone provider-detail
    endpoint, hence the separate fetch_print_provider_location()."""

    @patch("requests.Session.request")
    def test_variants_merges_is_enabled_from_two_requests(self, mock_request):
        full_response = mock_response(
            200,
            {
                "variants": [
                    {"id": 1, "options": {"color": "Black", "size": "M"}},
                    {"id": 2, "options": {"color": "Black", "size": "XL"}},
                ]
            },
        )
        in_stock_response = mock_response(200, {"variants": [{"id": 1, "options": {"color": "Black", "size": "M"}}]})
        mock_request.side_effect = [full_response, in_stock_response]

        client = PrintifyClient()
        data = fetch_print_provider_variants(client, blueprint_id=6, provider_id=402)

        by_id = {v["id"]: v for v in data["variants"]}
        self.assertTrue(by_id[1]["is_enabled"])
        self.assertFalse(by_id[2]["is_enabled"])

        # First call requests the full (incl. out-of-stock) catalogue; second is the default,
        # in-stock-only call used to determine which of those variants are actually available.
        first_call_params = mock_request.call_args_list[0].kwargs.get("params")
        second_call_params = mock_request.call_args_list[1].kwargs.get("params")
        self.assertEqual(first_call_params, {"show-out-of-stock": 1})
        self.assertIsNone(second_call_params)

    @patch("requests.Session.request")
    def test_provider_location_reads_standalone_endpoint(self, mock_request):
        mock_request.return_value = mock_response(200, {"id": 402, "title": "X", "location": {"country": "FR"}})
        client = PrintifyClient()
        location = fetch_print_provider_location(client, provider_id=402)
        self.assertEqual(location, {"country": "FR"})
        called_url = mock_request.call_args.args[1]
        self.assertIn("/catalog/print_providers/402.json", called_url)


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class SyncBlueprintsTests(TestCase):
    @patch("apps.printify.services.fetch_blueprints")
    def test_sync_blueprints_creates_rows(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": 1, "title": "Unisex Tee", "brand": "Gildan", "model": "5000", "images": ["http://x/1.png"]},
            {"id": 2, "title": "Hoodie", "brand": "Gildan", "model": "18500", "images": []},
        ]
        run = sync_blueprints()
        self.assertEqual(run.status, PrintifySyncRun.Status.SUCCESS)
        self.assertEqual(run.blueprints_synced, 2)
        self.assertEqual(PrintifyBlueprint.objects.count(), 2)
        tee = PrintifyBlueprint.objects.get(blueprint_id=1)
        self.assertEqual(tee.title, "Unisex Tee")
        self.assertEqual(tee.images, ["http://x/1.png"])

    @patch("apps.printify.services.fetch_blueprints")
    def test_sync_blueprints_upserts_and_preserves_mapping(self, mock_fetch):
        template = make_template()
        existing = PrintifyBlueprint.objects.create(blueprint_id=1, title="Old Title", mockup_template=template)

        mock_fetch.return_value = [{"id": 1, "title": "New Title", "brand": "Gildan", "model": "5000", "images": []}]
        run = sync_blueprints()

        self.assertEqual(run.status, PrintifySyncRun.Status.SUCCESS)
        existing.refresh_from_db()
        self.assertEqual(existing.title, "New Title")
        self.assertEqual(existing.mockup_template_id, template.id)

    @override_settings(PRINTIFY_API_TOKEN="")
    def test_sync_blueprints_not_configured_marks_run_failed(self):
        run = sync_blueprints()
        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIn("PRINTIFY_API_TOKEN", run.error_message)

    @patch("apps.printify.services.fetch_blueprints")
    def test_sync_blueprints_api_error_marks_run_failed(self, mock_fetch):
        mock_fetch.side_effect = PrintifyAPIError("boom", status_code=500)
        run = sync_blueprints()
        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIn("boom", run.error_message)
        self.assertEqual(PrintifyBlueprint.objects.count(), 0)


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class SyncPrintProvidersTests(TestCase):
    def setUp(self):
        self.blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")

    @patch("apps.printify.services.fetch_print_provider_location")
    @patch("apps.printify.services.fetch_print_provider_variants")
    @patch("apps.printify.services.fetch_print_providers")
    def test_sync_providers_creates_rows_with_variants(self, mock_providers, mock_variants, mock_location):
        mock_providers.return_value = [{"id": 10, "title": "Provider A"}]
        mock_variants.return_value = {
            "variants": [
                {"id": 100, "title": "Black / M", "options": {"color": "Black", "size": "M"}, "is_enabled": True},
                {"id": 101, "title": "Red / L", "options": {"color": "Red", "size": "L"}, "is_enabled": False},
            ]
        }
        mock_location.return_value = {"country": "US"}

        run = sync_print_providers_for_blueprint(self.blueprint)

        self.assertEqual(run.status, PrintifySyncRun.Status.SUCCESS)
        self.assertEqual(run.providers_synced, 1)
        self.assertEqual(run.variants_synced, 2)
        provider = PrintifyPrintProvider.objects.get(blueprint=self.blueprint, provider_id=10)
        self.assertEqual(provider.title, "Provider A")
        self.assertEqual(provider.location, {"country": "US"})
        self.assertEqual(len(provider.variants), 2)

    @patch("apps.printify.services.fetch_print_providers")
    def test_sync_providers_api_error_marks_run_failed(self, mock_providers):
        mock_providers.side_effect = PrintifyAPIError("unreachable", status_code=503)
        run = sync_print_providers_for_blueprint(self.blueprint)
        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIn("unreachable", run.error_message)


class ProductVariantSyncTests(TestCase):
    def setUp(self):
        self.template = make_template()
        self.product = make_shop_product(self.template)
        self.blueprint = PrintifyBlueprint.objects.create(
            blueprint_id=1, title="Unisex Tee", mockup_template=self.template
        )
        self.provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=10,
            title="Provider A",
            variants=[
                {
                    "id": 100,
                    "title": "Black / M",
                    "options": {"color": "Black", "size": "M"},
                    "is_enabled": True,
                    "placeholders": [{"position": "front"}],
                },
                {
                    "id": 101,
                    "title": "Red / L",
                    "options": {"color": "Red", "size": "L"},
                    "is_enabled": True,
                },
            ],
        )
        self.template.selected_print_provider = self.provider
        self.template.save(update_fields=["selected_print_provider"])

    def test_raises_when_no_provider_mapped(self):
        self.template.selected_print_provider = None
        self.template.save(update_fields=["selected_print_provider"])
        with self.assertRaises(Exception):
            sync_product_variants_from_printify(self.product)

    def test_creates_updates_and_disables_variants(self):
        existing_match = ProductVariant.objects.create(
            product=self.product, template=self.template, color_name="Black", size="M", retail_price="19.99"
        )
        existing_stale = ProductVariant.objects.create(
            product=self.product, template=self.template, color_name="Blue", size="S", retail_price="19.99"
        )

        summary = sync_product_variants_from_printify(self.product)

        self.assertEqual(
            summary,
            {"created": 1, "updated": 1, "skipped": 0, "unavailable": 1, "missing_cost": 2, "errors": []},
        )

        existing_match.refresh_from_db()
        self.assertEqual(existing_match.external_variant_id, "100")
        self.assertEqual(existing_match.external_provider, "Printify")
        self.assertTrue(existing_match.is_available)
        self.assertEqual(existing_match.supported_print_areas, ["front"])

        existing_stale.refresh_from_db()
        self.assertFalse(existing_stale.is_available)
        # Never deleted — the row (and any order/cart references to it) survives.
        self.assertTrue(ProductVariant.objects.filter(pk=existing_stale.pk).exists())

        new_variant = ProductVariant.objects.get(product=self.product, color_name="Red", size="L")
        self.assertEqual(new_variant.external_variant_id, "101")
        self.assertTrue(new_variant.is_available)

    def test_sync_preserves_manually_configured_retail_price(self):
        # Pricing is treated as local, admin-owned data — Printify's catalogue doesn't return
        # retail-ready cost data, so the sync must never overwrite what an admin already set.
        existing_match = ProductVariant.objects.create(
            product=self.product,
            template=self.template,
            color_name="Black",
            size="M",
            retail_price="49.99",
            base_cost="12.00",
        )
        sync_product_variants_from_printify(self.product)
        existing_match.refresh_from_db()
        self.assertEqual(str(existing_match.retail_price), "49.99")
        self.assertEqual(str(existing_match.base_cost), "12.00")
        # Non-pricing fields still update normally.
        self.assertEqual(existing_match.external_variant_id, "100")

    def test_sync_does_not_invent_a_price_for_newly_created_variants(self):
        sync_product_variants_from_printify(self.product)
        new_variant = ProductVariant.objects.get(product=self.product, color_name="Black", size="M")
        self.assertIsNone(new_variant.retail_price)
        self.assertIsNone(new_variant.base_cost)

    def test_synced_variants_always_belong_to_a_product(self):
        sync_product_variants_from_printify(self.product)
        self.assertFalse(ProductVariant.objects.filter(product__isnull=True).exists())
        for variant in ProductVariant.objects.filter(template=self.template):
            self.assertEqual(variant.product_id, self.product.id)

    def test_duplicate_sync_does_not_duplicate_variants(self):
        sync_product_variants_from_printify(self.product)
        first_count = ProductVariant.objects.filter(product=self.product).count()
        summary = sync_product_variants_from_printify(self.product)
        second_count = ProductVariant.objects.filter(product=self.product).count()
        self.assertEqual(first_count, second_count)
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["updated"], 2)


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class PrintifyAdminAPITests(APITestCase):
    def setUp(self):
        # Every Printify admin endpoint is IsSuperUser-gated (tightened from IsAdminUser once the
        # React admin panel — itself superuser-only — became the primary consumer; see
        # PrintifyConnectionStatusView's docstring). `self.staff` here is a full superuser despite
        # the name, matching the rest of this test suite's convention; `test_staff_without_
        # superuser_is_denied` below is what actually exercises the is_staff-but-not-superuser
        # boundary.
        self.staff = make_user("staff", is_staff=True)
        self.staff.is_superuser = True
        self.staff.save(update_fields=["is_superuser"])
        self.staff_only = make_user("staff-only", is_staff=True)
        self.regular = make_user("regular", is_staff=False)
        self.blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")

    def test_status_endpoint_requires_admin(self):
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.regular)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_superuser_is_denied(self):
        # is_staff=True alone must no longer be enough for any Printify admin endpoint — only
        # is_superuser=True (see PrintifyConnectionStatusView's docstring on the permission
        # tightening).
        self.client.force_authenticate(user=self.staff_only)
        self.assertEqual(self.client.get("/api/printify/status/").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get("/api/printify/blueprints/").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.post("/api/printify/sync-blueprints/").status_code, status.HTTP_403_FORBIDDEN
        )

    @patch("apps.printify.services.fetch_shops")
    def test_status_endpoint_connected(self, mock_fetch_shops):
        mock_fetch_shops.return_value = [{"id": 12345, "title": "Test Shop", "sales_channel": "disconnected"}]
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["configured"])
        self.assertTrue(response.data["connected"])
        self.assertEqual(response.data["shop"], {"id": 12345, "title": "Test Shop", "sales_channel": "disconnected"})
        self.assertIsNone(response.data["error"])
        self.assertEqual(response.data["blueprint_count"], 1)

    def test_blueprint_list_requires_admin(self):
        self.client.force_authenticate(user=self.regular)
        response = self.client.get("/api/printify/blueprints/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_blueprint_list_provider_count_query_does_not_scale_with_blueprint_count(self):
        # provider_count is served from an annotation (Count("print_providers")), not a
        # per-row obj.print_providers.count() call — query count must stay flat as rows grow.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(3):
            blueprint = PrintifyBlueprint.objects.create(blueprint_id=100 + i, title=f"Extra {i}")
            PrintifyPrintProvider.objects.create(blueprint=blueprint, provider_id=200 + i, title="Provider")

        self.client.force_authenticate(user=self.staff)

        with CaptureQueriesContext(connection) as baseline:
            response = self.client.get("/api/printify/blueprints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Paginated envelope (StandardResultsSetPagination) — the default page_size (24) still
        # fits every row created below on a single page, so `results` is the full set, not a
        # truncated one.
        self.assertEqual(len(response.data["results"]), 4)  # self.blueprint (setUp) + 3 just created
        baseline_count = len(baseline.captured_queries)

        for i in range(3, 8):
            blueprint = PrintifyBlueprint.objects.create(blueprint_id=100 + i, title=f"Extra {i}")
            PrintifyPrintProvider.objects.create(blueprint=blueprint, provider_id=200 + i, title="Provider")

        with CaptureQueriesContext(connection) as scaled:
            response = self.client.get("/api/printify/blueprints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 9)
        scaled_count = len(scaled.captured_queries)

        self.assertEqual(
            baseline_count,
            scaled_count,
            f"Blueprint list query count grew with row count (N+1): {baseline_count} vs {scaled_count}.",
        )
        # Sanity check the annotation actually drives the value, not a coincidental match.
        extra_row = next(r for r in response.data["results"] if r["blueprint_id"] == 107)
        self.assertEqual(extra_row["provider_count"], 1)

    def test_map_and_unmap_blueprint_to_template(self):
        template = make_template()
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            f"/api/printify/blueprints/{self.blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.blueprint.refresh_from_db()
        self.assertEqual(self.blueprint.mockup_template_id, template.id)

        response = self.client.post(
            f"/api/printify/blueprints/{self.blueprint.id}/map/", {"mockup_template_id": None}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.blueprint.refresh_from_db()
        self.assertIsNone(self.blueprint.mockup_template_id)

    def test_map_blueprint_rejects_nonexistent_template(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{self.blueprint.id}/map/", {"mockup_template_id": 999999}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(PRINTIFY_API_TOKEN="", PRINTIFY_ENABLED=True)
    def test_status_endpoint_missing_configuration(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["configured"])
        self.assertFalse(response.data["connected"])
        self.assertIsNone(response.data["shop"])
        self.assertIn("PRINTIFY_API_TOKEN", response.data["error"])

    @patch("apps.printify.services.fetch_shops")
    def test_status_endpoint_invalid_integration(self, mock_fetch_shops):
        mock_fetch_shops.side_effect = PrintifyAPIError("Printify API returned 401", status_code=401)
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["configured"])
        self.assertFalse(response.data["connected"])
        self.assertIsNone(response.data["shop"])
        self.assertIsNotNone(response.data["error"])

    @patch("apps.printify.services.fetch_shops")
    def test_status_endpoint_stable_shape_and_no_token_exposure(self, mock_fetch_shops):
        mock_fetch_shops.return_value = [{"id": 12345, "title": "Test Shop", "sales_channel": "disconnected"}]
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(
            set(response.data.keys()),
            {"configured", "connected", "shop", "error", "blueprint_count", "mapped_blueprint_count", "last_sync_run"},
        )
        self.assertNotIn("test-token", str(response.data))
        self.assertNotIn("Authorization", str(response.data))

    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_sync_blueprints_endpoint(self, mock_shops, mock_fetch):
        mock_shops.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        mock_fetch.return_value = [{"id": 2, "title": "New Blueprint", "brand": "", "model": "", "images": []}]
        self.client.force_authenticate(user=self.staff)
        response = self.client.post("/api/printify/sync-blueprints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "success")
        self.assertTrue(PrintifyBlueprint.objects.filter(blueprint_id=2).exists())

    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_sync_blueprints_endpoint_rejects_invalid_shop_before_syncing(self, mock_shops, mock_fetch):
        mock_shops.side_effect = PrintifyAPIError("Printify API returned 401 for GET /shops.json", status_code=401)
        self.client.force_authenticate(user=self.staff)
        response = self.client.post("/api/printify/sync-blueprints/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        mock_fetch.assert_not_called()

    @patch("apps.printify.services.fetch_print_provider_location")
    @patch("apps.printify.services.fetch_print_provider_variants")
    @patch("apps.printify.services.fetch_print_providers")
    @patch("apps.printify.services.fetch_shops")
    def test_sync_providers_endpoint_validates_shop_first(self, mock_shops, mock_providers, mock_variants, mock_location):
        mock_shops.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        mock_providers.return_value = [{"id": 10, "title": "Provider A"}]
        mock_variants.return_value = {"variants": []}
        mock_location.return_value = {}
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(f"/api/printify/blueprints/{self.blueprint.id}/sync-providers/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_shops.assert_called_once()

    @patch("apps.printify.services.fetch_print_providers")
    @patch("apps.printify.services.fetch_shops")
    def test_sync_providers_endpoint_rejects_invalid_shop_before_syncing(self, mock_shops, mock_providers):
        mock_shops.side_effect = PrintifyAPIError("Printify API returned 401 for GET /shops.json", status_code=401)
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(f"/api/printify/blueprints/{self.blueprint.id}/sync-providers/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        mock_providers.assert_not_called()


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class PrintifyAdminActionShopValidationTests(TestCase):
    """The 'Sync print providers...' Django admin action previously called
    sync_print_providers_for_blueprint directly with no shop check at all — the same class of
    bypass the sync_printify_catalogue command and the API views guard against. Exercised via
    the real admin changelist POST (Django admin uses session auth, not the DRF JWT layer the
    other API tests use, so this goes through Client.force_login + the actual admin URL)."""

    def setUp(self):
        self.staff = make_user("admin-action-staff", is_staff=True)
        self.staff.is_superuser = True
        self.staff.save(update_fields=["is_superuser"])
        self.blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        self.client.force_login(self.staff)

    def _post_action(self):
        # Deliberately not following the redirect: rendering the resulting changelist page
        # requires a built static-files manifest, which isn't available in this test
        # environment and is unrelated to what's being tested here. The 302 itself is Django
        # admin's normal "action processed" response — enough to prove the action ran (or
        # didn't) without needing to render the page it redirects to.
        return self.client.post(
            "/admin/printify/printifyblueprint/",
            {
                "action": "sync_print_providers",
                "_selected_action": [str(self.blueprint.pk)],
                "index": "0",
            },
        )

    @patch("apps.printify.services.fetch_print_provider_location")
    @patch("apps.printify.services.fetch_print_provider_variants")
    @patch("apps.printify.services.fetch_print_providers")
    @patch("apps.printify.services.fetch_shops")
    def test_admin_action_validates_shop_first(self, mock_shops, mock_providers, mock_variants, mock_location):
        mock_shops.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        mock_providers.return_value = []
        mock_variants.return_value = {"variants": []}
        mock_location.return_value = {}

        response = self._post_action()

        self.assertEqual(response.status_code, 302)  # admin redirects back to the changelist after processing
        mock_shops.assert_called_once()

    @patch("apps.printify.services.fetch_print_providers")
    @patch("apps.printify.services.fetch_shops")
    def test_admin_action_rejects_invalid_shop_before_syncing(self, mock_shops, mock_providers):
        mock_shops.side_effect = PrintifyAPIError("Printify API returned 401 for GET /shops.json", status_code=401)

        response = self._post_action()

        self.assertEqual(response.status_code, 302)  # admin still redirects; the error is an admin message, not a 500
        mock_providers.assert_not_called()
        self.assertEqual(PrintifySyncRun.objects.count(), 0)  # no sync run was ever created


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class FetchShopsTests(TestCase):
    @patch("requests.Session.request")
    def test_successful_list_response(self, mock_request):
        mock_request.return_value = mock_response(200, [{"id": 1, "title": "Shop A"}])
        client = PrintifyClient()
        shops = fetch_shops(client)
        self.assertEqual(shops, [{"id": 1, "title": "Shop A"}])

    @patch("requests.Session.request")
    def test_unexpected_non_list_response_raises(self, mock_request):
        mock_request.return_value = mock_response(200, {"error": "not a list"})
        client = PrintifyClient()
        with self.assertRaises(PrintifyResponseError):
            fetch_shops(client)

    @patch("requests.Session.request")
    def test_empty_shop_list(self, mock_request):
        mock_request.return_value = mock_response(200, [])
        client = PrintifyClient()
        self.assertEqual(fetch_shops(client), [])

    @patch("requests.Session.request")
    def test_api_error_propagates(self, mock_request):
        mock_request.return_value = mock_response(401, {"error": "invalid token"})
        client = PrintifyClient()
        with self.assertRaises(PrintifyAPIError) as ctx:
            fetch_shops(client)
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("time.sleep", return_value=None)
    @patch("requests.Session.request")
    def test_network_timeout_propagates_as_api_error(self, mock_request, mock_sleep):
        import requests as requests_module

        mock_request.side_effect = requests_module.exceptions.Timeout("timed out")
        client = PrintifyClient()
        with self.assertRaises(PrintifyAPIError):
            fetch_shops(client)


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class ValidateConfiguredShopTests(TestCase):
    @patch("apps.printify.services.fetch_shops")
    def test_configured_shop_found_integer_id(self, mock_fetch):
        mock_fetch.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        shop = validate_configured_shop()
        self.assertEqual(shop, {"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"})

    @patch("apps.printify.services.fetch_shops")
    def test_configured_shop_found_string_id(self, mock_fetch):
        # PRINTIFY_SHOP_ID is "12345" (a string, as all env vars are); Printify's real API
        # returns numeric IDs — this proves the string/int normalization actually matches.
        mock_fetch.return_value = [{"id": "12345", "title": "Artverse API Store", "sales_channel": ""}]
        shop = validate_configured_shop()
        self.assertEqual(shop["id"], "12345")

    @patch("apps.printify.services.fetch_shops")
    def test_configured_shop_not_found(self, mock_fetch):
        mock_fetch.return_value = [{"id": 99999, "title": "Someone Else's Shop", "sales_channel": ""}]
        with self.assertRaises(PrintifyShopNotFoundError):
            validate_configured_shop()

    @override_settings(PRINTIFY_SHOP_ID="")
    def test_missing_shop_id_raises_not_configured(self):
        with self.assertRaises(PrintifyNotConfiguredError) as ctx:
            validate_configured_shop()
        self.assertIn("PRINTIFY_SHOP_ID", str(ctx.exception))

    @override_settings(PRINTIFY_API_TOKEN="")
    def test_missing_token_raises_not_configured(self):
        with self.assertRaises(PrintifyNotConfiguredError) as ctx:
            validate_configured_shop()
        self.assertIn("PRINTIFY_API_TOKEN", str(ctx.exception))

    @patch("apps.printify.services.fetch_shops")
    def test_invalid_credentials_propagates(self, mock_fetch):
        mock_fetch.side_effect = PrintifyAPIError("Printify API returned 401", status_code=401)
        with self.assertRaises(PrintifyAPIError):
            validate_configured_shop()

    @patch("apps.printify.services.fetch_shops")
    def test_inaccessible_shop_is_shop_not_found(self, mock_fetch):
        # A token that's valid but can't see the configured shop looks the same to us as a
        # shop that doesn't exist — Printify's shops.json only ever lists what the token can see.
        mock_fetch.return_value = []
        with self.assertRaises(PrintifyShopNotFoundError):
            validate_configured_shop()


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class PrintifyTestConnectionCommandTests(TestCase):
    @patch("apps.printify.services.fetch_shops")
    def test_successful_output(self, mock_fetch):
        mock_fetch.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        out = StringIO()
        call_command("printify_test_connection", stdout=out)
        output = out.getvalue()
        self.assertIn("Printify connection successful.", output)
        self.assertIn("Artverse API Store", output)
        self.assertIn("12345", output)
        self.assertIn("disconnected", output)
        self.assertNotIn("test-token", output)

    @override_settings(PRINTIFY_API_TOKEN="")
    def test_missing_configuration_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("printify_test_connection", stdout=StringIO())
        self.assertIn("PRINTIFY_API_TOKEN", str(ctx.exception))

    @patch("apps.printify.services.fetch_shops")
    def test_invalid_token_raises_command_error(self, mock_fetch):
        mock_fetch.side_effect = PrintifyAPIError("Printify API returned 401", status_code=401)
        with self.assertRaises(CommandError):
            call_command("printify_test_connection", stdout=StringIO())

    @patch("apps.printify.services.fetch_shops")
    def test_shop_not_found_raises_command_error(self, mock_fetch):
        mock_fetch.return_value = [{"id": 1, "title": "Other Shop"}]
        with self.assertRaises(CommandError) as ctx:
            call_command("printify_test_connection", stdout=StringIO())
        self.assertIn("12345", str(ctx.exception))

    @patch("apps.printify.services.fetch_shops")
    def test_token_never_appears_in_output_even_on_success(self, mock_fetch):
        mock_fetch.return_value = [{"id": 12345, "title": "Store", "sales_channel": ""}]
        out = StringIO()
        call_command("printify_test_connection", stdout=out)
        self.assertNotIn("test-token", out.getvalue())
        self.assertNotIn("Bearer", out.getvalue())


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
class SyncPrintifyCatalogueCommandConnectionCheckTests(TestCase):
    """sync_printify_catalogue must validate the connection *before* touching any catalogue
    endpoint — these tests specifically prove the catalogue sync functions are never even
    called when that pre-flight check fails, not just that the command errors out somehow."""

    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_valid_shop_allows_sync_to_begin(self, mock_shops, mock_blueprints):
        mock_shops.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        mock_blueprints.return_value = [{"id": 1, "title": "A", "brand": "", "model": "", "images": []}]

        out = StringIO()
        call_command("sync_printify_catalogue", stdout=out)

        mock_blueprints.assert_called_once()
        output = out.getvalue()
        self.assertIn("Artverse API Store", output)
        self.assertIn("12345", output)

    @override_settings(PRINTIFY_API_TOKEN="")
    @patch("apps.printify.services.fetch_blueprints")
    def test_missing_token_stops_command_before_sync(self, mock_blueprints):
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_printify_catalogue", stdout=StringIO())
        self.assertIn("PRINTIFY_API_TOKEN", str(ctx.exception))
        mock_blueprints.assert_not_called()

    @override_settings(PRINTIFY_SHOP_ID="")
    @patch("apps.printify.services.fetch_blueprints")
    def test_missing_shop_id_stops_command_before_sync(self, mock_blueprints):
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_printify_catalogue", stdout=StringIO())
        self.assertIn("PRINTIFY_SHOP_ID", str(ctx.exception))
        mock_blueprints.assert_not_called()

    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_inaccessible_shop_stops_command_before_sync(self, mock_shops, mock_blueprints):
        mock_shops.return_value = [{"id": 99999, "title": "Someone Else's Shop"}]
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_printify_catalogue", stdout=StringIO())
        self.assertIn("12345", str(ctx.exception))
        mock_blueprints.assert_not_called()

    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_invalid_token_stops_command_before_sync(self, mock_shops, mock_blueprints):
        mock_shops.side_effect = PrintifyAPIError("Printify API returned 401", status_code=401)
        with self.assertRaises(CommandError):
            call_command("sync_printify_catalogue", stdout=StringIO())
        mock_blueprints.assert_not_called()

    @patch("apps.printify.services.fetch_shops")
    def test_token_never_appears_in_output_or_errors(self, mock_shops):
        mock_shops.side_effect = PrintifyAPIError("Printify API returned 401", status_code=401)
        out = StringIO()
        try:
            call_command("sync_printify_catalogue", stdout=out)
        except CommandError as exc:
            self.assertNotIn("test-token", str(exc))
        self.assertNotIn("test-token", out.getvalue())

    @override_settings(DEBUG=True)
    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_skip_connection_check_flag_bypasses_validation_when_debug_true(self, mock_shops, mock_blueprints):
        mock_blueprints.return_value = []
        out = StringIO()
        call_command("sync_printify_catalogue", "--skip-connection-check", stdout=out)
        mock_shops.assert_not_called()
        mock_blueprints.assert_called_once()
        self.assertIn("Printify connection validation was skipped because DEBUG=True.", out.getvalue())

    @override_settings(DEBUG=False)
    @patch("apps.printify.services.sync_print_providers_for_blueprint")
    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_skip_connection_check_rejected_when_debug_false(self, mock_shops, mock_blueprints, mock_sync_providers):
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_printify_catalogue", "--skip-connection-check", stdout=StringIO())
        self.assertIn("DEBUG=True", str(ctx.exception))
        mock_shops.assert_not_called()
        mock_blueprints.assert_not_called()
        mock_sync_providers.assert_not_called()
        self.assertEqual(PrintifySyncRun.objects.count(), 0)  # no partial sync run created

    @override_settings(DEBUG=False)
    @patch("apps.printify.services.fetch_blueprints")
    @patch("apps.printify.services.fetch_shops")
    def test_default_behavior_validates_shop_before_sync_regardless_of_debug(self, mock_shops, mock_blueprints):
        mock_shops.return_value = [{"id": 12345, "title": "Artverse API Store", "sales_channel": "disconnected"}]
        mock_blueprints.return_value = []
        out = StringIO()
        call_command("sync_printify_catalogue", stdout=out)
        mock_shops.assert_called_once()
        mock_blueprints.assert_called_once()
        self.assertIn("Artverse API Store", out.getvalue())
        self.assertIn("12345", out.getvalue())

    @override_settings(DEBUG=False)
    def test_skip_connection_check_error_never_contains_token(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_printify_catalogue", "--skip-connection-check", stdout=StringIO())
        self.assertNotIn("test-token", str(ctx.exception))
        self.assertNotIn("Bearer", str(ctx.exception))


class ProviderBlueprintConsistencyTests(TestCase):
    def setUp(self):
        self.template = make_template()
        self.blueprint = PrintifyBlueprint.objects.create(
            blueprint_id=1, title="Unisex Tee", mockup_template=self.template
        )
        self.matching_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint, provider_id=10, title="Matching Provider"
        )
        self.other_blueprint = PrintifyBlueprint.objects.create(blueprint_id=2, title="Hoodie")
        self.mismatched_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.other_blueprint, provider_id=20, title="Mismatched Provider"
        )

    def test_template_can_select_provider_from_mapped_blueprint(self):
        self.template.selected_print_provider = self.matching_provider
        self.template.full_clean()  # should not raise

    def test_template_cannot_select_provider_from_another_blueprint(self):
        self.template.selected_print_provider = self.mismatched_provider
        with self.assertRaises(ValidationError) as ctx:
            self.template.full_clean()
        self.assertIn("selected_print_provider", ctx.exception.message_dict)

    def test_selected_provider_rejected_when_template_has_no_mapped_blueprint(self):
        # A provider is only ever valid relative to a blueprint mapping — with none mapped at
        # all, no provider selection can be confirmed consistent, so it's rejected outright.
        unmapped_template = make_template(slug="unmapped-template")
        unmapped_template.selected_print_provider = self.mismatched_provider
        with self.assertRaises(ValidationError) as ctx:
            unmapped_template.full_clean()
        self.assertIn("selected_print_provider", ctx.exception.message_dict)

    def test_validate_provider_matches_template_blueprint_helper_directly(self):
        validate_provider_matches_template_blueprint(self.template.pk, self.matching_provider)
        with self.assertRaises(ValueError):
            validate_provider_matches_template_blueprint(self.template.pk, self.mismatched_provider)

    def test_local_variant_sync_rejects_mismatched_provider(self):
        # Force an inconsistent state the way a bulk update or fixture load could, bypassing
        # model.clean() entirely — the sync service must catch this independently.
        self.template.selected_print_provider = self.mismatched_provider
        self.template.save(update_fields=["selected_print_provider"])
        product = make_shop_product(self.template, slug="mismatched-product")
        with self.assertRaises(Exception):
            sync_product_variants_from_printify(product)

    def test_changing_blueprint_invalidates_unrelated_selected_provider(self):
        self.template.selected_print_provider = self.matching_provider
        self.template.full_clean()
        self.template.save()

        # Re-map the template to a different blueprint — the previously-valid provider now
        # belongs to a blueprint that's no longer mapped here.
        self.blueprint.mockup_template = None
        self.blueprint.save(update_fields=["mockup_template"])
        self.other_blueprint.mockup_template = self.template
        self.other_blueprint.save(update_fields=["mockup_template"])

        self.template.refresh_from_db()
        # selected_print_provider (matching_provider, blueprint 1) is now inconsistent with the
        # newly-mapped blueprint (blueprint 2) — full_clean() must reveal this.
        with self.assertRaises(ValidationError):
            self.template.full_clean()

    def test_admin_form_rejects_inconsistent_provider_mapping(self):
        from apps.generator.admin import MockupTemplateAdminForm

        form = MockupTemplateAdminForm(
            data={
                "name": self.template.name,
                "slug": self.template.slug,
                "product_type": self.template.product_type,
                "description": "",
                "is_active": True,
                "template_version": 1,
                "config": "{}",
                "supported_colors": "[]",
                "supported_sizes": "[]",
                "supported_file_formats": "[]",
                "selected_print_provider": self.mismatched_provider.pk,
            },
            instance=self.template,
        )
        # The form's own queryset filtering already excludes the mismatched provider, so this
        # should fail as an invalid choice (not a silent pass) — proving the dropdown is scoped,
        # not just decorative.
        self.assertFalse(form.is_valid())
        self.assertIn("selected_print_provider", form.errors)

    def test_valid_selected_provider_survives_catalogue_resync(self):
        self.template.selected_print_provider = self.matching_provider
        self.template.full_clean()
        self.template.save()

        with patch("apps.printify.services.fetch_print_providers") as mock_providers, patch(
            "apps.printify.services.fetch_print_provider_variants"
        ) as mock_variants, patch("apps.printify.services.fetch_print_provider_location") as mock_location, override_settings(
            PRINTIFY_API_TOKEN="test-token", PRINTIFY_ENABLED=True
        ):
            mock_providers.return_value = [{"id": 10, "title": "Matching Provider (renamed)"}]
            mock_variants.return_value = {"variants": []}
            mock_location.return_value = {}
            run = sync_print_providers_for_blueprint(self.blueprint)

        self.assertEqual(run.status, PrintifySyncRun.Status.SUCCESS)
        self.template.refresh_from_db()
        self.assertEqual(self.template.selected_print_provider_id, self.matching_provider.pk)


class PlaceholderValidationTests(TestCase):
    def setUp(self):
        self.template = make_template()
        self.blueprint = PrintifyBlueprint.objects.create(
            blueprint_id=1, title="Unisex Tee", mockup_template=self.template
        )
        self.provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=10,
            title="Provider A",
            variants=[
                {
                    "id": 100,
                    "options": {"color": "Black", "size": "M"},
                    "placeholders": [
                        {"position": "front", "width": 4200, "height": 4800},
                        {"position": "back", "width": 4200, "height": 4800},
                    ],
                },
                {
                    "id": 101,
                    "options": {"color": "Black", "size": "L"},
                    "placeholders": [{"position": "left_sleeve", "width": 1181, "height": 1181}],
                },
            ],
        )
        self.template.selected_print_provider = self.provider
        self.template.save(update_fields=["selected_print_provider"])

    def test_get_provider_placeholder_positions(self):
        self.assertEqual(
            get_provider_placeholder_positions(self.provider), {"front", "back", "left_sleeve"}
        )

    def test_valid_front_mapping(self):
        validate_placeholder_position("front", self.provider)  # should not raise

    def test_valid_back_mapping(self):
        validate_placeholder_position("back", self.provider)  # should not raise

    def test_valid_sleeve_mapping(self):
        validate_placeholder_position("left_sleeve", self.provider)  # should not raise

    def test_blank_mapping_always_allowed(self):
        validate_placeholder_position("", self.provider)  # no-op, should not raise
        part = MockupTemplatePart(template=self.template, name=MockupTemplatePart.PartName.BACK)
        part.full_clean(exclude=["base_image"])  # blank printify_placeholder_position, should not raise

    def test_invalid_placeholder_position_rejected(self):
        with self.assertRaises(ValueError):
            validate_placeholder_position("inside_label", self.provider)

    def test_placeholder_not_offered_by_selected_provider_rejected_via_model(self):
        part = MockupTemplatePart(
            template=self.template,
            name=MockupTemplatePart.PartName.RIGHT_SLEEVE,
            printify_placeholder_position="right_sleeve",  # not in this provider's catalogue
        )
        with self.assertRaises(ValidationError) as ctx:
            part.full_clean(exclude=["base_image"])
        self.assertIn("printify_placeholder_position", ctx.exception.message_dict)

    def test_provider_change_makes_existing_placeholder_invalid(self):
        part = MockupTemplatePart.objects.create(
            template=self.template, name=MockupTemplatePart.PartName.LEFT_SLEEVE, printify_placeholder_position="left_sleeve"
        )
        part.full_clean(exclude=["base_image"])  # valid against the current provider

        other_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=11,
            title="Provider B (no sleeve printing)",
            variants=[{"id": 200, "options": {"color": "Black", "size": "M"}, "placeholders": [{"position": "front"}]}],
        )
        self.template.selected_print_provider = other_provider
        self.template.save(update_fields=["selected_print_provider"])

        part.refresh_from_db()
        with self.assertRaises(ValidationError):
            part.full_clean(exclude=["base_image"])

    def test_missing_placeholders_array_tolerated(self):
        provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=12,
            title="Provider C",
            variants=[{"id": 300, "options": {"color": "Black", "size": "M"}}],  # no "placeholders" key at all
        )
        self.assertEqual(get_provider_placeholder_positions(provider), set())

    def test_duplicate_placeholder_data_handled_safely(self):
        provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=13,
            title="Provider D",
            variants=[
                {"id": 400, "options": {"color": "Black", "size": "M"}, "placeholders": [{"position": "front"}]},
                {"id": 401, "options": {"color": "Black", "size": "L"}, "placeholders": [{"position": "front"}]},
                {"id": 402, "options": {"color": "Red", "size": "M"}, "placeholders": [{"position": "front"}]},
            ],
        )
        # Three variants all offering "front" — the result is still just {"front"}, not inflated.
        self.assertEqual(get_provider_placeholder_positions(provider), {"front"})

    def test_valid_right_sleeve_mapping(self):
        provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=14,
            title="Provider E",
            variants=[
                {"id": 500, "options": {"color": "Black", "size": "M"}, "placeholders": [{"position": "right_sleeve"}]}
            ],
        )
        validate_placeholder_position("right_sleeve", provider)  # should not raise

    def test_valid_provider_specific_custom_placeholder(self):
        # Real Printify data includes positions like "neck"/"inside_label" that aren't among our
        # own front/back/sleeve part names — validation must accept whatever the provider offers.
        provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=15,
            title="Provider F",
            variants=[{"id": 600, "options": {"color": "Black", "size": "M"}, "placeholders": [{"position": "neck"}]}],
        )
        validate_placeholder_position("neck", provider)  # should not raise

    def test_explicit_placeholder_rejected_when_provider_has_no_synced_variants(self):
        # A provider row only exists after a sync, but its variant catalogue can still come back
        # empty (or with no placeholders on any variant) — that must not be treated as "anything
        # goes"; an explicit mapping against it can't be verified, so it's rejected.
        unsynced_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint, provider_id=16, title="Never really synced", variants=[]
        )
        with self.assertRaises(ValueError) as ctx:
            validate_placeholder_position("front", unsynced_provider)
        self.assertIn("not been synchronized", str(ctx.exception))

    def test_explicit_placeholder_rejected_via_model_when_provider_unsynced(self):
        unsynced_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint, provider_id=17, title="Never really synced", variants=[]
        )
        self.template.selected_print_provider = unsynced_provider
        self.template.save(update_fields=["selected_print_provider"])

        part = MockupTemplatePart(
            template=self.template, name=MockupTemplatePart.PartName.FRONT, printify_placeholder_position="front"
        )
        with self.assertRaises(ValidationError) as ctx:
            part.full_clean(exclude=["base_image"])
        self.assertIn("printify_placeholder_position", ctx.exception.message_dict)

    def test_blank_placeholder_still_allowed_when_provider_unsynced(self):
        unsynced_provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint, provider_id=18, title="Never really synced", variants=[]
        )
        self.template.selected_print_provider = unsynced_provider
        self.template.save(update_fields=["selected_print_provider"])

        # make_template() already creates a FRONT part, so use BACK to avoid a unique-together clash.
        part = MockupTemplatePart(template=self.template, name=MockupTemplatePart.PartName.BACK)
        part.full_clean(exclude=["base_image"])  # blank — should not raise even though provider is unsynced

    def test_explicit_placeholder_rejected_when_no_provider_selected(self):
        # make_template() already creates a FRONT part, so use BACK to keep this test isolated
        # to the placeholder/provider check rather than also tripping a unique-together error.
        template = make_template(slug="no-provider-template")
        part = MockupTemplatePart(
            template=template, name=MockupTemplatePart.PartName.BACK, printify_placeholder_position="back"
        )
        with self.assertRaises(ValueError):
            validate_placeholder_position("front", None)
        with self.assertRaises(ValidationError) as ctx:
            part.full_clean(exclude=["base_image"])
        self.assertIn("printify_placeholder_position", ctx.exception.message_dict)

    def test_malformed_variants_json_handled_safely(self):
        provider = PrintifyPrintProvider.objects.create(
            blueprint=self.blueprint,
            provider_id=19,
            title="Provider G",
            variants=[
                "not a dict",
                {"id": 700, "options": {"color": "Black", "size": "M"}, "placeholders": "not a list"},
                {"id": 701, "options": {"color": "Black", "size": "L"}, "placeholders": [123, "not a dict either"]},
                {"id": 702, "options": {"color": "Black", "size": "XL"}, "placeholders": [{"position": "front"}]},
            ],
        )
        # Only the one well-formed entry contributes; the rest are safely ignored, not crashed on.
        self.assertEqual(get_provider_placeholder_positions(provider), {"front"})
        validate_placeholder_position("front", provider)  # should not raise
        with self.assertRaises(ValueError):
            validate_placeholder_position("back", provider)


class TransactionAndFailureStateTests(TestCase):
    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.PrintifyBlueprint.objects.update_or_create")
    @patch("apps.printify.services.fetch_blueprints")
    def test_blueprint_persistence_rolls_back_on_database_failure(self, mock_fetch, mock_upsert):
        mock_fetch.return_value = [
            {"id": 1, "title": "A", "brand": "", "model": "", "images": []},
            {"id": 2, "title": "B", "brand": "", "model": "", "images": []},
        ]
        mock_upsert.side_effect = [(Mock(), True), RuntimeError("db exploded")]

        with self.assertRaises(RuntimeError):
            sync_blueprints()

        self.assertEqual(PrintifyBlueprint.objects.count(), 0)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.PrintifyPrintProvider.objects.update_or_create")
    @patch("apps.printify.services.fetch_print_provider_location")
    @patch("apps.printify.services.fetch_print_provider_variants")
    @patch("apps.printify.services.fetch_print_providers")
    def test_provider_persistence_rolls_back_on_database_failure(
        self, mock_providers, mock_variants, mock_location, mock_upsert
    ):
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        mock_providers.return_value = [{"id": 10, "title": "A"}, {"id": 11, "title": "B"}]
        mock_variants.return_value = {"variants": []}
        mock_location.return_value = {}
        mock_upsert.side_effect = [(Mock(), True), RuntimeError("db exploded")]

        with self.assertRaises(RuntimeError):
            sync_print_providers_for_blueprint(blueprint)

        self.assertEqual(PrintifyPrintProvider.objects.filter(blueprint=blueprint).count(), 0)

    @patch("apps.generator.models.ProductVariant.objects.create")
    def test_local_variant_sync_rolls_back_on_failure(self, mock_create):
        template = make_template()
        product = make_shop_product(template)
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee", mockup_template=template)
        provider = PrintifyPrintProvider.objects.create(
            blueprint=blueprint,
            provider_id=10,
            title="Provider A",
            variants=[
                {"id": 100, "options": {"color": "Black", "size": "M"}, "is_enabled": True},
                {"id": 101, "options": {"color": "Red", "size": "L"}, "is_enabled": True},
            ],
        )
        template.selected_print_provider = provider
        template.save(update_fields=["selected_print_provider"])

        mock_create.side_effect = RuntimeError("disk full")

        with self.assertRaises(RuntimeError):
            sync_product_variants_from_printify(product)

        self.assertEqual(ProductVariant.objects.filter(product=product).count(), 0)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.fetch_blueprints")
    def test_unexpected_exception_marks_run_failed_and_reraises(self, mock_fetch):
        mock_fetch.side_effect = KeyError("boom")

        with self.assertRaises(KeyError):
            sync_blueprints()

        run = PrintifySyncRun.objects.latest("started_at")
        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIsNotNone(run.finished_at)
        self.assertIn("KeyError", run.error_message)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.fetch_print_providers")
    def test_unexpected_exception_in_provider_sync_marks_run_failed_and_reraises(self, mock_fetch):
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        mock_fetch.side_effect = TypeError("unexpected shape")

        with self.assertRaises(TypeError):
            sync_print_providers_for_blueprint(blueprint)

        run = PrintifySyncRun.objects.filter(blueprint=blueprint).latest("started_at")
        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIsNotNone(run.finished_at)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.fetch_blueprints")
    def test_known_printify_error_marks_run_failed_without_reraising(self, mock_fetch):
        mock_fetch.side_effect = PrintifyAPIError("boom", status_code=500)

        run = sync_blueprints()  # does not raise — existing callers rely on this

        self.assertEqual(run.status, PrintifySyncRun.Status.FAILED)
        self.assertIsNotNone(run.finished_at)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.fetch_blueprints")
    def test_successful_run_marked_success_with_finished_at(self, mock_fetch):
        mock_fetch.return_value = [{"id": 1, "title": "A", "brand": "", "model": "", "images": []}]

        run = sync_blueprints()

        self.assertEqual(run.status, PrintifySyncRun.Status.SUCCESS)
        self.assertIsNotNone(run.finished_at)

    @override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345", PRINTIFY_ENABLED=True)
    @patch("apps.printify.services.fetch_blueprints")
    def test_no_run_remains_in_running_state_after_any_outcome(self, mock_fetch):
        # Success, known failure, and unexpected failure — none should leave a row "running".
        mock_fetch.return_value = [{"id": 1, "title": "A", "brand": "", "model": "", "images": []}]
        sync_blueprints()

        mock_fetch.side_effect = PrintifyAPIError("boom")
        sync_blueprints()

        mock_fetch.side_effect = ValueError("boom")
        with self.assertRaises(ValueError):
            sync_blueprints()

        self.assertEqual(PrintifySyncRun.objects.filter(status=PrintifySyncRun.Status.RUNNING).count(), 0)


class PrintifyBlueprintMapViewConsistencyTests(APITestCase):
    """The mapping endpoint (POST /api/printify/blueprints/<id>/map/) must not be able to leave
    a template pointing at a provider outside its mapped blueprint, or at a placeholder mapping
    that provider doesn't support — see PrintifyBlueprintMapView / validate_blueprint_mapping."""

    def setUp(self):
        self.staff = make_user("staff", is_staff=True)
        self.staff.is_superuser = True
        self.staff.save(update_fields=["is_superuser"])
        self.regular = make_user("regular", is_staff=False)

    def _synced_provider(self, blueprint, provider_id=10, positions=("front", "back")):
        return PrintifyPrintProvider.objects.create(
            blueprint=blueprint,
            provider_id=provider_id,
            title=f"Provider {provider_id}",
            variants=[
                {
                    "id": 1000 + provider_id,
                    "options": {"color": "Black", "size": "M"},
                    "placeholders": [{"position": p} for p in positions],
                }
            ],
        )

    # --- Valid mapping ---

    def test_maps_to_unconfigured_template(self):
        template = make_template(slug="unconfigured")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.mockup_template_id, template.id)

    def test_remapping_to_a_new_blueprint_unmaps_the_previous_one(self):
        # No provider selected on the template, so there's nothing to conflict with — this
        # isolates the "one blueprint per template" enforcement itself from provider validation.
        template = make_template(slug="reassign")
        blueprint_a = PrintifyBlueprint.objects.create(blueprint_id=1, title="Blueprint A", mockup_template=template)
        blueprint_b = PrintifyBlueprint.objects.create(blueprint_id=2, title="Blueprint B")

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint_b.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        blueprint_a.refresh_from_db()
        blueprint_b.refresh_from_db()
        self.assertIsNone(blueprint_a.mockup_template_id)  # auto-unmapped
        self.assertEqual(blueprint_b.mockup_template_id, template.id)

    def test_reaffirming_existing_consistent_mapping_succeeds_and_preserves_placeholders(self):
        template = make_template(slug="consistent")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee", mockup_template=template)
        provider = self._synced_provider(blueprint)
        template.selected_print_provider = provider
        template.save(update_fields=["selected_print_provider"])
        part = MockupTemplatePart.objects.get(template=template, name=MockupTemplatePart.PartName.FRONT)
        part.printify_placeholder_position = "front"
        part.save(update_fields=["printify_placeholder_position"])

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        part.refresh_from_db()
        self.assertEqual(part.printify_placeholder_position, "front")

    # --- Invalid selected provider ---

    def test_rejects_remap_when_template_provider_belongs_to_another_blueprint(self):
        template = make_template(slug="cross-blueprint")
        blueprint_a = PrintifyBlueprint.objects.create(blueprint_id=1, title="Blueprint A", mockup_template=template)
        provider_a = self._synced_provider(blueprint_a, provider_id=10)
        template.selected_print_provider = provider_a
        template.save(update_fields=["selected_print_provider"])

        blueprint_b = PrintifyBlueprint.objects.create(blueprint_id=2, title="Blueprint B")

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint_b.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("another Printify blueprint", response.data["detail"])

        blueprint_a.refresh_from_db()
        blueprint_b.refresh_from_db()
        template.refresh_from_db()
        self.assertEqual(blueprint_a.mockup_template_id, template.id)  # unchanged
        self.assertIsNone(blueprint_b.mockup_template_id)  # rejected mapping never applied
        self.assertEqual(template.selected_print_provider_id, provider_a.id)  # unchanged

    # --- Invalid placeholder mapping ---

    def test_rejects_remap_when_a_part_placeholder_is_unsupported(self):
        template = make_template(slug="bad-placeholder")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        provider = self._synced_provider(blueprint, positions=("front",))  # no "back"
        template.selected_print_provider = provider
        template.save(update_fields=["selected_print_provider"])
        part = MockupTemplatePart.objects.get(template=template, name=MockupTemplatePart.PartName.FRONT)
        part.printify_placeholder_position = "back"  # not offered by this provider
        part.save(update_fields=["printify_placeholder_position"])

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("invalid Printify placeholder", response.data["detail"])
        self.assertIn("front", response.data["errors"])  # keyed by part name

        blueprint.refresh_from_db()
        self.assertIsNone(blueprint.mockup_template_id)  # rejected mapping never applied
        part.refresh_from_db()
        self.assertEqual(part.printify_placeholder_position, "back")  # untouched by the failed attempt

    # --- Unsynchronized provider ---

    def test_rejects_remap_when_explicit_placeholder_set_but_provider_unsynced(self):
        template = make_template(slug="unsynced-provider")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        unsynced_provider = PrintifyPrintProvider.objects.create(
            blueprint=blueprint, provider_id=10, title="Unsynced", variants=[]
        )
        template.selected_print_provider = unsynced_provider
        template.save(update_fields=["selected_print_provider"])
        part = MockupTemplatePart.objects.get(template=template, name=MockupTemplatePart.PartName.FRONT)
        part.printify_placeholder_position = "front"
        part.save(update_fields=["printify_placeholder_position"])

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("front", response.data["errors"])
        self.assertIn("not been synchronized", response.data["errors"]["front"][0])

    def test_blank_placeholder_mapping_succeeds_even_with_unsynced_provider(self):
        template = make_template(slug="unsynced-provider-blank")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        unsynced_provider = PrintifyPrintProvider.objects.create(
            blueprint=blueprint, provider_id=10, title="Unsynced", variants=[]
        )
        template.selected_print_provider = unsynced_provider
        template.save(update_fields=["selected_print_provider"])
        # FRONT part left with a blank printify_placeholder_position (the default).

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    # --- Transaction rollback ---

    def test_mapping_rolls_back_on_unexpected_error_during_validation(self):
        template = make_template(slug="rollback-target")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")

        self.client.force_authenticate(user=self.staff)
        with patch(
            "apps.printify.views.validate_blueprint_mapping", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    f"/api/printify/blueprints/{blueprint.id}/map/",
                    {"mockup_template_id": template.id},
                    format="json",
                )

        blueprint.refresh_from_db()
        self.assertIsNone(blueprint.mockup_template_id)  # the tentative save was rolled back

    # --- Permissions ---

    def test_unauthenticated_cannot_map(self):
        template = make_template(slug="perm-anon")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_admin_cannot_map(self):
        template = make_template(slug="perm-regular")
        blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
        self.client.force_authenticate(user=self.regular)
        response = self.client.post(
            f"/api/printify/blueprints/{blueprint.id}/map/", {"mockup_template_id": template.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
