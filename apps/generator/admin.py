from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from PIL import Image

from .models import GeneratedImage, GenerationRequest, MockupRender, MockupTemplate, SourceDesignAsset
from .services import (
    hydrate_source_design_asset,
    resolve_source_asset_fingerprint,
)


class SourceDesignAssetAdminForm(forms.ModelForm):
    class Meta:
        model = SourceDesignAsset
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        artwork = cleaned_data.get("artwork")
        image = cleaned_data.get("image")
        source_url = (cleaned_data.get("source_url") or "").strip()

        if not image and not artwork and not source_url:
            raise ValidationError("Provide an uploaded image, choose an artwork, or supply a source URL.")

        if artwork and not image and not source_url and not (artwork.image or artwork.image_url):
            raise ValidationError(
                "The selected artwork has no image to inherit. Upload an image or add an image to the artwork first."
            )

        fingerprint = resolve_source_asset_fingerprint(
            artwork=artwork,
            source_image_url=source_url,
            uploaded_image=image,
            existing_fingerprint=cleaned_data.get("source_fingerprint", ""),
        )
        if fingerprint:
            existing = SourceDesignAsset.objects.exclude(pk=self.instance.pk).filter(source_fingerprint=fingerprint).first()
            if existing:
                raise ValidationError(
                    f"A source design asset already exists for this image source (asset #{existing.pk})."
                )
            cleaned_data["source_fingerprint"] = fingerprint

        return cleaned_data


@admin.register(GenerationRequest)
class GenerationRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "provider", "status", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("user__email", "prompt")


@admin.register(GeneratedImage)
class GeneratedImageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "generation_request", "created_at")
    search_fields = ("user__email", "prompt")


@admin.register(MockupTemplate)
class MockupTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "product_type", "template_version", "is_active", "updated_at")
    list_filter = ("product_type", "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    fields = (
        "name",
        "slug",
        "product_type",
        "description",
        "is_active",
        "base_image",
        "mask_image",
        "displacement_map",
        "shadow_layer",
        "highlight_layer",
        "template_version",
        "config",
        "supported_colors",
        "supported_sizes",
    )


@admin.register(SourceDesignAsset)
class SourceDesignAssetAdmin(admin.ModelAdmin):
    form = SourceDesignAssetAdminForm
    list_display = ("id", "title", "artwork", "source_fingerprint", "updated_at")
    search_fields = ("title", "source_url", "source_fingerprint", "artwork__title")
    readonly_fields = ("source_fingerprint", "width", "height", "created_at", "updated_at")
    fields = (
        "artwork",
        "title",
        "image",
        "source_url",
        "source_fingerprint",
        "width",
        "height",
        "notes",
        "created_at",
        "updated_at",
    )

    def save_model(self, request, obj, form, change):
        obj.source_fingerprint = form.cleaned_data.get("source_fingerprint", obj.source_fingerprint)
        if not obj.title and obj.artwork:
            obj.title = obj.artwork.title

        if not obj.image and (obj.artwork or obj.source_url):
            hydrated = hydrate_source_design_asset(
                obj,
                source_image_url=obj.source_url,
                artwork=obj.artwork,
                title=obj.title,
            )
            if hydrated is None:
                raise ValidationError(
                    "Could not resolve an image from the selected artwork or source URL."
                )
        elif obj.image and (obj.width is None or obj.height is None):
            obj.image.open("rb")
            try:
                with Image.open(obj.image) as image:
                    obj.width, obj.height = image.size
            finally:
                obj.image.close()

        super().save_model(request, obj, form, change)


@admin.register(MockupRender)
class MockupRenderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "template",
        "artwork",
        "status",
        "variant_color",
        "variant_size",
        "user",
        "created_at",
    )
    list_filter = ("status", "template__product_type")
    search_fields = (
        "template__name",
        "artwork__title",
        "user__email",
        "source_prompt",
        "source_image_url",
        "cache_key",
    )
