from rest_framework.permissions import BasePermission


class IsSuperUser(BasePermission):
    """Gates the custom admin management panel — deliberately stricter than DRF's own
    IsAdminUser (which only checks is_staff, the same flag that gates the customization
    editor's development/print-files panel). Every admin-panel-only endpoint uses this, not
    IsAdminUser, so a plain staff account never gets write access to catalog/pricing data."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)
