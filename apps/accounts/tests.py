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
