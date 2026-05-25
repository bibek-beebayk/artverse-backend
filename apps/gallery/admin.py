from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse

from .forms import ArtworkBulkUploadForm
from .models import Artwork, Category, Collection, Favorite, VideoClip
from .services import ArtworkBulkImporter


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "description")


@admin.register(Artwork)
class ArtworkAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "category", "collection", "is_featured", "is_published", "created_at")
    list_filter = ("category", "collection", "is_featured", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "description")
    change_list_template = "admin/gallery/artwork/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "bulk-upload/",
                self.admin_site.admin_view(self.bulk_upload_view),
                name="gallery_artwork_bulk_upload",
            ),
        ]
        return custom_urls + urls

    def bulk_upload_view(self, request):
        form = ArtworkBulkUploadForm(request.POST or None, request.FILES or None)
        import_result = None

        if request.method == "POST" and form.is_valid():
            importer = ArtworkBulkImporter(
                update_existing=form.cleaned_data["update_existing"],
                auto_create_categories=form.cleaned_data["auto_create_categories"],
                dry_run=form.cleaned_data["dry_run"],
            )
            try:
                import_result = importer.import_from_files(
                    csv_file=form.cleaned_data["csv_file"],
                    images_zip_file=form.cleaned_data.get("images_zip"),
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                if form.cleaned_data["dry_run"]:
                    messages.info(request, "Dry run completed. Review the import summary below.")
                else:
                    messages.success(
                        request,
                        (
                            f"Artwork import completed. Created: {import_result.created}, "
                            f"updated: {import_result.updated}, skipped: {import_result.skipped}, "
                            f"failed: {import_result.failed}."
                        ),
                    )
                if not form.cleaned_data["dry_run"] and import_result.failed == 0:
                    return redirect(reverse("admin:gallery_artwork_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Bulk Upload Artworks",
            "form": form,
            "import_result": import_result,
        }
        return render(request, "admin/gallery/artwork/bulk_upload.html", context)


@admin.register(VideoClip)
class VideoClipAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_published", "created_at")
    list_filter = ("is_published",)
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "artwork", "created_at")
    search_fields = ("user__email", "artwork__title")
