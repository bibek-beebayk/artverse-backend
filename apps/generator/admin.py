from django.contrib import admin

from .models import GeneratedImage, GenerationRequest


@admin.register(GenerationRequest)
class GenerationRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "provider", "status", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("user__email", "prompt")


@admin.register(GeneratedImage)
class GeneratedImageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "generation_request", "created_at")
    search_fields = ("user__email", "prompt")
