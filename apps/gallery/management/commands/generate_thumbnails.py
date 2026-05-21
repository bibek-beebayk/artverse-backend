from django.core.management.base import BaseCommand

from apps.gallery.models import Artwork
from apps.shop.models import Product


class Command(BaseCommand):
    help = "Generate missing thumbnails for artworks and products."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenerate thumbnails even when one already exists.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        artwork_count = 0
        product_count = 0

        artworks = Artwork.objects.exclude(image="").exclude(image__isnull=True)
        products = Product.objects.exclude(image="").exclude(image__isnull=True)

        for artwork in artworks.iterator():
            if force or not artwork.thumbnail:
                if artwork.regenerate_thumbnail(save=True):
                    artwork_count += 1

        for product in products.iterator():
            if force or not product.thumbnail:
                if product.regenerate_thumbnail(save=True):
                    product_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated thumbnails for {artwork_count} artworks and {product_count} products."
            )
        )
