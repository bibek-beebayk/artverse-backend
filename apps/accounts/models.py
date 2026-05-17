from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar = models.URLField(blank=True)
    is_artist = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.display_name or self.username or self.email


class SiteConfiguration(models.Model):
    maintenance_mode = models.BooleanField(default=False)
    maintenance_access_key = models.CharField(max_length=255, blank=True)
    maintenance_message = models.CharField(
        max_length=255,
        default="Artverse is currently offline for updates, polishing, and launch preparation. We will be back soon.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def __str__(self) -> str:
        return "Global Site Configuration"

    @classmethod
    def get_solo(cls) -> "SiteConfiguration":
        config, _ = cls.objects.get_or_create(pk=1)
        return config
