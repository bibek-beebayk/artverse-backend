from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import SiteConfiguration, User


@admin.register(User)
class ArtverseUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "Artverse Profile",
            {"fields": ("display_name", "avatar", "is_artist")},
        ),
    )
    list_display = ("id", "email", "username", "display_name", "is_artist", "is_staff")
    search_fields = ("email", "username", "display_name")


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Maintenance Mode",
            {
                "fields": (
                    "maintenance_mode",
                    "maintenance_access_key",
                    "maintenance_message",
                    "updated_at",
                )
            },
        ),
    )
    readonly_fields = ("updated_at",)
    list_display = ("maintenance_mode", "updated_at")

    def has_add_permission(self, request):
        if SiteConfiguration.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
