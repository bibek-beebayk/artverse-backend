import json

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from PIL import Image

from .models import (
    DesignPlacement,
    DesignProject,
    GeneratedImage,
    GenerationRequest,
    MockupRender,
    MockupTemplate,
    MockupTemplatePart,
    ProductVariant,
    SourceDesignAsset,
)
from .services import (
    hydrate_source_design_asset,
    resolve_source_asset_fingerprint,
)


class MockupConfigEditorWidget(forms.Textarea):
    template_name = "admin/generator/widgets/mockup_config_editor.html"

    class Media:
        css = {
            "all": ("generator/admin/mockup_config_editor.css",),
        }
        js = ("generator/admin/mockup_config_editor.js",)

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.base_image_url = ""

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["base_image_url"] = self.base_image_url
        context["widget"]["empty_config"] = json.dumps(
            {
                "placement": {
                    "x": 100,
                    "y": 100,
                    "width": 300,
                    "height": 300,
                    "fit": "contain",
                    "rotation": 0,
                    "opacity": 1,
                    "corner_radius": 0,
                },
                "sample_placements": [],
            }
        )
        return context


class MockupTemplateAdminForm(forms.ModelForm):
    class Meta:
        model = MockupTemplate
        fields = "__all__"
        widgets = {
            "config": MockupConfigEditorWidget(
                attrs={
                    "rows": 16,
                    "class": "vLargeTextField",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        widget = self.fields["config"].widget
        if isinstance(widget, MockupConfigEditorWidget):
            if self.instance.pk and self.instance.base_image:
                try:
                    widget.base_image_url = self.instance.base_image.url
                except Exception:
                    widget.base_image_url = ""
            self.fields["config"].help_text = (
                "Use the visual placement editor below to drag and resize the print area. "
                "The JSON stays available for advanced tuning."
            )


class MockupTemplatePartForm(forms.ModelForm):
    class Meta:
        model = MockupTemplatePart
        fields = "__all__"
        widgets = {
            "config": MockupConfigEditorWidget(
                attrs={
                    "rows": 16,
                    "class": "vLargeTextField",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        widget = self.fields["config"].widget
        if isinstance(widget, MockupConfigEditorWidget):
            if self.instance.pk and self.instance.base_image:
                try:
                    widget.base_image_url = self.instance.base_image.url
                except Exception:
                    widget.base_image_url = ""
            self.fields["config"].help_text = (
                "Use the visual placement editor below to drag and resize the print area for this part."
            )


class MockupTemplatePartInline(admin.StackedInline):
    model = MockupTemplatePart
    form = MockupTemplatePartForm
    extra = 0


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fk_name = "template"
    fields = (
        "product",
        "sku",
        "color_name",
        "color_hex",
        "size",
        "base_cost",
        "retail_price",
        "inventory",
        "is_available",
        "external_provider",
        "external_variant_id",
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
    form = MockupTemplateAdminForm
    list_display = ("id", "name", "product_type", "template_version", "is_active", "updated_at")
    list_filter = ("product_type", "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MockupTemplatePartInline, ProductVariantInline]
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
        "canvas_width",
        "canvas_height",
        "supported_file_formats",
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


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product",
        "template",
        "sku",
        "color_name",
        "size",
        "base_cost",
        "retail_price",
        "inventory",
        "is_available",
        "updated_at",
    )
    list_filter = ("is_available", "template__product_type", "external_provider")
    search_fields = ("template__name", "product__name", "sku", "color_name", "size", "external_variant_id")
    list_select_related = ("product", "template")


class DesignPlacementInline(admin.StackedInline):
    model = DesignPlacement
    extra = 0
    fk_name = "design_project"


@admin.register(DesignProject)
class DesignProjectAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "user",
        "product",
        "mockup_template",
        "selected_variant",
        "status",
        "updated_at",
    )
    list_filter = ("status", "mockup_template__product_type")
    search_fields = ("name", "user__email", "mockup_template__name", "product__name")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("user", "product", "mockup_template", "selected_variant")
    inlines = [DesignPlacementInline]
