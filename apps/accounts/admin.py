from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


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
