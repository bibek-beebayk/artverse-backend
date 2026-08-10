from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import SiteConfiguration


User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "display_name", "avatar", "is_artist", "is_staff", "is_superuser")
        read_only_fields = ("id", "is_artist", "is_staff", "is_superuser")


class AdminUserSerializer(serializers.ModelSerializer):
    """Admin-panel-only — unlike UserSerializer above, `is_staff`/`is_superuser`/`is_artist` are
    writable here (that's the entire point of the Users & Access screen), but identity fields
    (username/email/password) are deliberately still read-only — this is a permissions-flag
    editor, not a full user-account editor. Only ever used behind IsSuperUser."""

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "display_name",
            "avatar",
            "is_artist",
            "is_staff",
            "is_superuser",
            "is_active",
            "date_joined",
        )
        read_only_fields = ("id", "username", "email", "display_name", "avatar", "date_joined")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("id", "username", "email", "display_name", "password")
        read_only_fields = ("id",)

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField()


class SiteConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteConfiguration
        fields = ("maintenance_mode", "maintenance_access_key", "maintenance_message", "updated_at")
        read_only_fields = ("updated_at",)
