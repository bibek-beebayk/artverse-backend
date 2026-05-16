from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.gallery.models import Artwork, Category, VideoClip
from apps.shop.models import Product, ProductCategory


ARTWORKS = [
    {
        "title": "Neon Nexus",
        "category": "Cyberpunk",
        "image_url": "https://images.unsplash.com/photo-1605142859862-978be7eba909?auto=format&fit=crop&q=80&w=1200",
        "description": "A sprawling cityscape illuminated by neon rain.",
        "is_featured": True,
    },
    {
        "title": "Velocity Prime",
        "category": "Futuristic Vehicles",
        "image_url": "https://images.unsplash.com/photo-1590362891991-f776e747a588?auto=format&fit=crop&q=80&w=1200",
        "description": "The pinnacle of aerodynamic AI engineering.",
        "is_featured": True,
    },
    {
        "title": "Etheria Guardian",
        "category": "AI Characters",
        "image_url": "https://images.unsplash.com/photo-1614728263952-84ea256f9679?auto=format&fit=crop&q=80&w=1200",
        "description": "A digitized consciousness guarding the data stream.",
        "is_featured": True,
    },
    {
        "title": "Void Drifter",
        "category": "Cyberpunk",
        "image_url": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&q=80&w=1200",
        "description": "Wandering the neon-lit alleys of Sector 7.",
        "is_featured": True,
    },
    {
        "title": "Aura of the Abyss",
        "category": "Fantasy Worlds",
        "image_url": "https://images.unsplash.com/photo-1605806616949-1e87b487fc2f?auto=format&fit=crop&q=80&w=1200",
        "description": "Where magic meeting silicon in the depths.",
        "is_featured": False,
    },
    {
        "title": "Carbon Knight",
        "category": "AI Characters",
        "image_url": "https://images.unsplash.com/photo-1544197150-b99a580bb7a8?auto=format&fit=crop&q=80&w=1200",
        "description": "The future of medieval combat, reinforced with graphene.",
        "is_featured": False,
    },
]

VIDEOS = [
    {
        "title": "Cyber City Rain",
        "thumbnail_url": "https://images.unsplash.com/photo-1614728263952-84ea256f9679?auto=format&fit=crop&q=80&w=800",
        "video_url": "https://assets.mixkit.co/videos/preview/mixkit-cyberpunk-style-city-street-ambience-39871-large.mp4",
    },
    {
        "title": "Neon Pulse",
        "thumbnail_url": "https://images.unsplash.com/photo-1605142859862-978be7eba909?auto=format&fit=crop&q=80&w=800",
        "video_url": "https://assets.mixkit.co/videos/preview/mixkit-futuristic-urban-landscape-at-night-with-neon-lights-40011-large.mp4",
    },
    {
        "title": "Droid Awakening",
        "thumbnail_url": "https://images.unsplash.com/photo-1544197150-b99a580bb7a8?auto=format&fit=crop&q=80&w=800",
        "video_url": "https://assets.mixkit.co/videos/preview/mixkit-robot-factory-assembly-line-40021-large.mp4",
    },
]

PRODUCTS = [
    {
        "name": "Artverse Elite T-Shirt",
        "category": "T-shirts",
        "price": Decimal("35.00"),
        "image_url": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?auto=format&fit=crop&q=80&w=800",
    },
    {
        "name": "Cyberpunk Skyline Poster",
        "category": "Posters",
        "price": Decimal("25.00"),
        "image_url": "https://images.unsplash.com/photo-1541963463532-d68292c34b19?auto=format&fit=crop&q=80&w=800",
    },
    {
        "name": "Neon Voyager Mug",
        "category": "Mugs",
        "price": Decimal("18.00"),
        "image_url": "https://images.unsplash.com/photo-1572113173140-5152ee53f8af?auto=format&fit=crop&q=80&w=800",
    },
]


class Command(BaseCommand):
    help = "Seed the local database with the sample Artverse content used by the frontend."

    def handle(self, *args, **options):
        for artwork_data in ARTWORKS:
            category, _ = Category.objects.get_or_create(
                name=artwork_data["category"],
                defaults={"slug": slugify(artwork_data["category"])},
            )
            Artwork.objects.update_or_create(
                slug=slugify(artwork_data["title"]),
                defaults={
                    "title": artwork_data["title"],
                    "category": category,
                    "description": artwork_data["description"],
                    "image_url": artwork_data["image_url"],
                    "is_featured": artwork_data["is_featured"],
                    "is_published": True,
                },
            )

        for video_data in VIDEOS:
            VideoClip.objects.update_or_create(
                slug=slugify(video_data["title"]),
                defaults=video_data,
            )

        for product_data in PRODUCTS:
            category, _ = ProductCategory.objects.get_or_create(
                name=product_data["category"],
                defaults={"slug": slugify(product_data["category"])},
            )
            Product.objects.update_or_create(
                slug=slugify(product_data["name"]),
                defaults={
                    "name": product_data["name"],
                    "category": category,
                    "price": product_data["price"],
                    "image_url": product_data["image_url"],
                    "description": "",
                    "inventory": 0,
                    "is_active": True,
                },
            )

        self.stdout.write(self.style.SUCCESS("Artverse sample content seeded successfully."))
