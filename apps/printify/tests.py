from unittest.mock import Mock, patch

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
    fetch_print_provider_location,
    fetch_print_provider_variants,
    sync_blueprints,
    sync_print_providers_for_blueprint,
    sync_product_variants_from_printify,
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
        name="Test Product", slug=slug, category=category, price="19.99", mockup_template=template
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


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345")
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


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345")
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


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345")
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


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345")
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
        self.blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")
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

        self.assertEqual(summary, {"created": 1, "updated": 1, "marked_unavailable": 1})

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


@override_settings(PRINTIFY_API_TOKEN="test-token", PRINTIFY_SHOP_ID="12345")
class PrintifyAdminAPITests(APITestCase):
    def setUp(self):
        self.staff = make_user("staff", is_staff=True)
        self.regular = make_user("regular", is_staff=False)
        self.blueprint = PrintifyBlueprint.objects.create(blueprint_id=1, title="Unisex Tee")

    def test_status_endpoint_requires_admin(self):
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.regular)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/printify/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_configured"])
        self.assertEqual(response.data["blueprint_count"], 1)

    def test_blueprint_list_requires_admin(self):
        self.client.force_authenticate(user=self.regular)
        response = self.client.get("/api/printify/blueprints/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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

    @patch("apps.printify.services.fetch_blueprints")
    def test_sync_blueprints_endpoint(self, mock_fetch):
        mock_fetch.return_value = [{"id": 2, "title": "New Blueprint", "brand": "", "model": "", "images": []}]
        self.client.force_authenticate(user=self.staff)
        response = self.client.post("/api/printify/sync-blueprints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "success")
        self.assertTrue(PrintifyBlueprint.objects.filter(blueprint_id=2).exists())
