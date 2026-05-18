from django.core.management.base import BaseCommand

from apps.gallery.models import Artwork
from apps.generator.services import ensure_source_design_asset, resolve_source_fingerprint


class Command(BaseCommand):
    help = "Cache gallery artwork images into SourceDesignAsset storage for mockup rendering."

    def add_arguments(self, parser):
        parser.add_argument("--artwork-id", type=int, help="Cache a single artwork by id.")
        parser.add_argument(
            "--include-local",
            action="store_true",
            help="Also create cache assets for artworks that already have a stored image file.",
        )

    def handle(self, *args, **options):
        queryset = Artwork.objects.all().order_by("id")
        artwork_id = options.get("artwork_id")
        include_local = options.get("include_local", False)

        if artwork_id:
            queryset = queryset.filter(pk=artwork_id)

        created_or_updated = 0
        skipped = 0

        for artwork in queryset:
            if not artwork.image and not artwork.image_url:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Skipping artwork {artwork.pk}: no image source."))
                continue

            if artwork.image and not include_local:
                skipped += 1
                self.stdout.write(
                    self.style.NOTICE(
                        f"Skipping artwork {artwork.pk}: already stored locally. Use --include-local to cache it too."
                    )
                )
                continue

            fingerprint = resolve_source_fingerprint(artwork=artwork)
            asset = ensure_source_design_asset(
                source_fingerprint=fingerprint,
                source_image_url=artwork.image_url,
                artwork=artwork,
                title=artwork.title,
            )
            if asset is None:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Skipping artwork {artwork.pk}: asset could not be created."))
                continue

            created_or_updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cached artwork {artwork.pk} -> source asset {asset.pk} ({asset.width}x{asset.height})"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished caching source assets. Created or updated: {created_or_updated}. Skipped: {skipped}."
            )
        )
