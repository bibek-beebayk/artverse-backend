from rest_framework import status
from rest_framework.test import APITestCase

from .models import SiteConfiguration, User


def make_user(username="tester", **overrides):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="testpass123", **overrides)


class IsSuperUserPermissionTests(APITestCase):
    """The admin management panel's core gate — deliberately stricter than DRF's IsAdminUser
    (is_staff), which already gates the customization editor's dev-tools panel elsewhere. Uses
    the accounts admin endpoints as the representative check; every other app's admin/* endpoints
    share this exact permission class, so this is not accounts-specific behaviour."""

    def test_anonymous_is_rejected(self):
        response = self.client.get("/api/auth/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_staff_but_not_superuser_is_rejected(self):
        staff = make_user("staffonly", is_staff=True, is_superuser=False)
        self.client.force_authenticate(user=staff)
        response = self.client.get("/api/auth/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_is_accepted(self):
        superuser = make_user("superadmin", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=superuser)
        response = self.client.get("/api/auth/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_regular_user_is_rejected(self):
        regular = make_user("regular")
        self.client.force_authenticate(user=regular)
        response = self.client.get("/api/auth/admin/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AdminUserFlagEditingTests(APITestCase):
    """Users & Access — flags only, identity fields stay read-only even for a superuser caller."""

    def setUp(self):
        self.superuser = make_user("superadmin2", is_staff=True, is_superuser=True)
        self.target = make_user("plainuser")
        self.client.force_authenticate(user=self.superuser)

    def test_can_grant_staff_flag(self):
        response = self.client.patch(f"/api/auth/admin/users/{self.target.id}/", {"is_staff": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_staff)

    def test_username_is_not_writable(self):
        response = self.client.patch(
            f"/api/auth/admin/users/{self.target.id}/", {"username": "renamed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.target.refresh_from_db()
        self.assertEqual(self.target.username, "plainuser")

    def test_search_filters_by_username(self):
        make_user("findme")
        response = self.client.get("/api/auth/admin/users/?search=findme")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = {row["username"] for row in response.data}
        self.assertIn("findme", usernames)
        self.assertNotIn("plainuser", usernames)


class AdminSiteConfigurationSingletonTests(APITestCase):
    def setUp(self):
        self.superuser = make_user("superadmin3", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.superuser)

    def test_get_returns_solo_instance(self):
        response = self.client.get("/api/auth/admin/site-configuration/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(SiteConfiguration.objects.count(), 1)

    def test_patch_updates_solo_instance(self):
        response = self.client.patch(
            "/api/auth/admin/site-configuration/", {"maintenance_mode": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(SiteConfiguration.get_solo().maintenance_mode)


class AdminDashboardTests(APITestCase):
    """The Dashboard's aggregated summary endpoint — superuser-gated like every other admin
    endpoint, shape-checked (no accidental leak of per-user detail), and query-count-bounded
    (a handful of aggregate queries, never one per row of any table)."""

    def setUp(self):
        self.superuser = make_user("dashboardadmin", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.superuser)

    def test_requires_superuser(self):
        self.client.logout()
        response = self.client.get("/api/auth/admin/dashboard/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        staff_only = make_user("dashboardstaffonly", is_staff=True, is_superuser=False)
        self.client.force_authenticate(user=staff_only)
        response = self.client.get("/api/auth/admin/dashboard/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_response_shape_has_no_fabricated_commerce_metrics(self):
        response = self.client.get("/api/auth/admin/dashboard/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()), {"products", "variants", "printify", "generator", "commerce", "users"}
        )
        self.assertEqual(
            set(response.data["products"].keys()), {"active", "inactive", "needs_attention"}
        )
        self.assertEqual(set(response.data["variants"].keys()), {"sellable", "missing_cost", "unavailable"})
        self.assertEqual(set(response.data["commerce"].keys()), {"current_carts"})
        # No "revenue"/"sales"/"orders" key anywhere — those systems don't exist yet.
        flattened_keys = {k for section in response.data.values() for k in section}
        for forbidden in ("revenue", "sales", "orders", "total_price"):
            self.assertNotIn(forbidden, flattened_keys)

    def test_counts_reflect_actual_data(self):
        from apps.generator.models import MockupTemplate, MockupTemplatePart, ProductVariant
        from apps.shop.models import Product, ProductCategory

        template = MockupTemplate.objects.create(
            name="Dashboard Template", slug="dashboard-template", product_type=MockupTemplate.ProductType.TSHIRT
        )
        MockupTemplatePart.objects.create(template=template, name=MockupTemplatePart.PartName.FRONT)
        category = ProductCategory.objects.create(name="Dashboard Cat", slug="dashboard-cat")

        active_product = Product.objects.create(
            name="Active", slug="dashboard-active", category=category, mockup_template=template, is_active=True
        )
        ProductVariant.objects.create(
            product=active_product, template=template, color_name="Black", size="M", base_cost="10.00"
        )
        Product.objects.create(
            name="Inactive", slug="dashboard-inactive", category=category, mockup_template=template, is_active=False
        )

        response = self.client.get("/api/auth/admin/dashboard/")
        self.assertGreaterEqual(response.data["products"]["active"], 1)
        self.assertGreaterEqual(response.data["products"]["inactive"], 1)
        self.assertGreaterEqual(response.data["variants"]["sellable"], 1)
        self.assertGreaterEqual(response.data["users"]["total"], 1)

    def test_query_count_is_bounded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/auth/admin/dashboard/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # A handful of aggregate COUNT queries, not dozens — generous headroom against a future
        # regression turning one of these into a per-row Python loop.
        self.assertLess(len(queries.captured_queries), 20)
