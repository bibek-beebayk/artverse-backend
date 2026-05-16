from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar = models.URLField(blank=True)
    is_artist = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.display_name or self.username or self.email
