from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Artwork, Category, Collection, VideoClip


def make_category(slug="gallery-test-cat"):
    return Category.objects.create(name=f"Category {slug}", slug=slug)


def make_artwork(*, title="Test Artwork", slug="test-artwork", category=None, is_published=True, **overrides):
    category = category or make_category(slug=f"cat-{slug}")
    return Artwork.objects.create(
        title=title, slug=slug, category=category, is_published=is_published, **overrides
    )


class GalleryPaginationTests(APITestCase):
    """The customization screen's "Choose from Gallery" selector calls this endpoint directly —
    it must be paginated (never the whole catalogue in one response), support search, and never
    leak an unpublished (not admin-approved) design."""

    def test_pagination_metadata_shape(self):
        for i in range(3):
            make_artwork(title=f"Art {i}", slug=f"pg-art-{i}")

        response = self.client.get("/api/gallery/artworks/?page_size=2")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.data
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 2)
        self.assertEqual(body["total_pages"], 2)
        self.assertEqual(len(body["results"]), 2)
        self.assertIsNotNone(body["next"])

    def test_only_published_designs_returned(self):
        make_artwork(title="Visible", slug="pg-visible")
        make_artwork(title="Hidden", slug="pg-hidden", is_published=False)

        response = self.client.get("/api/gallery/artworks/")
        slugs = {row["slug"] for row in response.data["results"]}
        self.assertEqual(slugs, {"pg-visible"})
        self.assertEqual(response.data["count"], 1)

    def test_search_matches_title_or_description(self):
        make_artwork(title="Cyber Punk", slug="pg-cyber")
        matched = make_artwork(title="Something Else", slug="pg-other")
        matched.description = "totally cyber vibes"
        matched.save(update_fields=["description"])
        make_artwork(title="Unrelated", slug="pg-unrelated")

        response = self.client.get("/api/gallery/artworks/?search=cyber")
        slugs = {row["slug"] for row in response.data["results"]}
        self.assertEqual(slugs, {"pg-cyber", "pg-other"})

    def test_category_filter_still_works(self):
        cat_a = make_category(slug="pg-cat-a")
        cat_b = make_category(slug="pg-cat-b")
        make_artwork(title="A", slug="pg-a", category=cat_a)
        make_artwork(title="B", slug="pg-b", category=cat_b)

        response = self.client.get("/api/gallery/artworks/?category=pg-cat-a")
        slugs = {row["slug"] for row in response.data["results"]}
        self.assertEqual(slugs, {"pg-a"})

    def test_ordering_by_title(self):
        make_artwork(title="Zebra", slug="pg-zebra")
        make_artwork(title="Alpha", slug="pg-alpha")

        response = self.client.get("/api/gallery/artworks/?ordering=title")
        self.assertEqual([row["slug"] for row in response.data["results"]], ["pg-alpha", "pg-zebra"])

    def test_unknown_ordering_value_is_ignored(self):
        make_artwork(title="A", slug="pg-order-a")
        response = self.client.get("/api/gallery/artworks/?ordering=__class__.mro")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AutoSlugGenerationTests(TestCase):
    """A blank slug on create is auto-generated from the natural title/name field
    (config.slug_utils.unique_slugify), and colliding names get a unique `-2`, `-3`, ... suffix
    rather than a database IntegrityError."""

    def test_category_slug_generated_from_name(self):
        category = Category.objects.create(name="Cyberpunk Art")
        self.assertEqual(category.slug, "cyberpunk-art")

    def test_collection_slug_generated_and_unique_on_collision(self):
        first = Collection.objects.create(name="Neon Dreams")
        second = Collection.objects.create(name="Neon Dreams!!")  # slugifies to the same base
        self.assertEqual(first.slug, "neon-dreams")
        self.assertEqual(second.slug, "neon-dreams-2")

    def test_artwork_slug_generated_from_title_and_unique(self):
        category = Category.objects.create(name="Auto Slug Cat")
        first = Artwork.objects.create(title="Sunset City", category=category)
        second = Artwork.objects.create(title="Sunset City", category=category)
        third = Artwork.objects.create(title="Sunset City", category=category)
        self.assertEqual(first.slug, "sunset-city")
        self.assertEqual(second.slug, "sunset-city-2")
        self.assertEqual(third.slug, "sunset-city-3")

    def test_video_clip_slug_generated_from_title(self):
        clip = VideoClip.objects.create(title="Behind The Scenes", video_url="https://example.com/v.mp4")
        self.assertEqual(clip.slug, "behind-the-scenes")

    def test_explicit_slug_is_not_overridden(self):
        category = Category.objects.create(name="Custom Slug Test", slug="totally-custom")
        self.assertEqual(category.slug, "totally-custom")

    def test_updating_other_fields_does_not_regenerate_existing_slug(self):
        category = Category.objects.create(name="Stable Slug")
        original_slug = category.slug
        category.name = "Stable Slug Renamed"
        category.save()
        self.assertEqual(category.slug, original_slug)
