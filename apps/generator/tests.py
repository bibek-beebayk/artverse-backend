import tempfile
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.gallery.models import Artwork, Category
from apps.shop.models import Product, ProductCategory

from .models import (
    DesignPlacement,
    DesignProject,
    GeneratedImage,
    MockupRender,
    MockupTemplate,
    MockupTemplatePart,
    ProductVariant,
)


def attach_image(field_file, filename="test.png"):
    buffer = BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buffer, format="PNG")
    field_file.save(filename, ContentFile(buffer.getvalue()), save=True)


def make_template(slug="tshirt-test", with_parts=True, **overrides):
    template = MockupTemplate.objects.create(
        name=overrides.pop("name", "Test Tshirt"),
        slug=slug,
        product_type=MockupTemplate.ProductType.TSHIRT,
        is_active=True,
        config={"placement": {"x": 100, "y": 100, "width": 200, "height": 200, "fit": "contain"}},
        **overrides,
    )
    if with_parts:
        for part_name in (MockupTemplatePart.PartName.FRONT, MockupTemplatePart.PartName.BACK):
            MockupTemplatePart.objects.create(
                template=template,
                name=part_name,
                config={"placement": {"x": 10, "y": 10, "width": 50, "height": 50}},
            )
    return template


def make_shop_product(template, slug="test-product"):
    category = ProductCategory.objects.create(name=f"Cat {slug}", slug=f"cat-{slug}")
    return Product.objects.create(
        name="Test Product", slug=slug, category=category, price="19.99", mockup_template=template
    )


def make_variant(template, product=None, color_name="Black", size="M", **overrides):
    return ProductVariant.objects.create(
        template=template, product=product, color_name=color_name, size=size, retail_price="19.99", **overrides
    )


def make_user(username="tester", email=None):
    return User.objects.create_user(username=username, email=email or f"{username}@example.com", password="testpass123")


class DesignProjectModelTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()

    def test_create_project_and_placement(self):
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template)
        placement = DesignPlacement.objects.create(design_project=project, part_name="front")
        self.assertEqual(placement.design_project_id, project.id)

    def test_one_placement_per_project_part(self):
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template)
        DesignPlacement.objects.create(design_project=project, part_name="front")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DesignPlacement.objects.create(design_project=project, part_name="front")

    def test_product_and_template_relationship(self):
        product = make_shop_product(self.template)
        self.assertEqual(product.mockup_template_id, self.template.id)
        self.assertIn(product, self.template.shop_products.all())

    def test_product_variant_uniqueness(self):
        make_variant(self.template, color_name="Black", size="M")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_variant(self.template, color_name="Black", size="M")

    def test_cascade_deletion_of_placements(self):
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template)
        DesignPlacement.objects.create(design_project=project, part_name="front")
        project.delete()
        self.assertEqual(DesignPlacement.objects.count(), 0)

    def test_retains_shared_artwork_on_placement_delete(self):
        category = Category.objects.create(name="Cat", slug="cat")
        artwork = Artwork.objects.create(title="Art", slug="art", category=category)
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template)
        placement = DesignPlacement.objects.create(design_project=project, part_name="front", source_artwork=artwork)
        placement.delete()
        artwork.refresh_from_db()
        self.assertTrue(Artwork.objects.filter(pk=artwork.pk).exists())

    def test_retains_shared_generated_image_on_project_delete(self):
        image = GeneratedImage.objects.create(user=self.user, prompt="p")
        project = DesignProject.objects.create(
            user=self.user, mockup_template=self.template, source_generated_image=image
        )
        project.delete()
        self.assertTrue(GeneratedImage.objects.filter(pk=image.pk).exists())

    def test_ordering_is_most_recently_updated_first(self):
        older = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="Older")
        newer = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="Newer")
        self.assertEqual(list(DesignProject.objects.all())[:2], [newer, older])

    def test_default_name_generated_when_blank(self):
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template)
        self.assertIn("T-Shirt", project.name)

    def test_status_choices_are_draft_ready_archived(self):
        self.assertEqual(
            [c[0] for c in DesignProject.Status.choices],
            ["draft", "ready", "archived"],
        )


class DesignProjectOwnershipTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.other = make_user("other")
        self.template = make_template()
        self.project = DesignProject.objects.create(user=self.owner, mockup_template=self.template, name="Mine")

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_other_user_cannot_list_owners_project(self):
        self._auth(self.other)
        response = self.client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_other_user_cannot_retrieve(self):
        self._auth(self.other)
        response = self.client.get(f"/api/generator/design-projects/{self.project.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_user_cannot_update(self):
        self._auth(self.other)
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project.id}/", {"name": "Hijacked"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Mine")

    def test_other_user_cannot_duplicate(self):
        self._auth(self.other)
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/duplicate/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_user_cannot_delete(self):
        self._auth(self.other)
        response = self.client.delete(f"/api/generator/design-projects/{self.project.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(DesignProject.objects.filter(pk=self.project.pk).exists())

    def test_unauthenticated_requires_login(self):
        response = self.client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_client_cannot_set_user_field(self):
        self._auth(self.other)
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": self.template.id, "user": self.owner.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = DesignProject.objects.get(pk=response.data["id"])
        self.assertEqual(created.user_id, self.other.id)


class DesignProjectNestedCreateTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.client.force_authenticate(user=self.user)

    def test_create_with_front_back_placements_preserves_all_fields(self):
        payload = {
            "name": "Dragon Tee",
            "mockup_template_id": self.template.id,
            "placements": [
                {
                    "part_name": "front",
                    "placement_override": {
                        "x": 10, "y": 20, "width": 300, "height": 400,
                        "rotation": 15, "opacity": 0.8, "corner_radius": 12, "fit": "cover",
                    },
                    "crop_override": {"left": 5, "top": 5, "width": 90, "height": 90},
                    "text_elements": [{"text": "HI", "fontFamily": "Roboto", "color": "#fff", "fontSize": 24, "x": 1, "y": 1, "rotation": 0}],
                    "metadata": {"note": "front layer"},
                },
                {
                    "part_name": "back",
                    "placement_override": {"x": 1, "y": 2, "width": 100, "height": 150},
                },
            ],
        }
        response = self.client.post("/api/generator/design-projects/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        project = DesignProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.placements.count(), 2)

        front = project.placements.get(part_name="front")
        self.assertEqual(front.x, 10)
        self.assertEqual(front.y, 20)
        self.assertEqual(front.width, 300)
        self.assertEqual(front.height, 400)
        self.assertEqual(front.rotation, 15)
        self.assertEqual(front.opacity, 0.8)
        self.assertEqual(front.corner_radius, 12)
        self.assertEqual(front.fit, "cover")
        self.assertEqual(front.crop_left, 5)
        self.assertEqual(front.crop_width, 90)
        self.assertEqual(front.text_elements[0]["text"], "HI")
        self.assertEqual(front.metadata["note"], "front layer")
        self.assertIsNotNone(front.template_part_id)

        back = project.placements.get(part_name="back")
        self.assertEqual(back.width, 100)

    def test_create_stamps_schema_version_into_metadata(self):
        response = self.client.post(
            "/api/generator/design-projects/", {"mockup_template_id": self.template.id}, format="json"
        )
        project = DesignProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.metadata.get("schema_version"), 1)

    def test_preview_render_and_source_references_preserved(self):
        render = MockupRender.objects.create(
            template=self.template, source_fingerprint="fp", cache_key="ck1", status="ready"
        )
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template.id,
                "placements": [{"part_name": "front", "preview_render_id": render.id, "preview_url": "https://example.com/p.png"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        front = DesignProject.objects.get(pk=response.data["id"]).placements.get(part_name="front")
        self.assertEqual(front.preview_render_id, render.id)
        self.assertEqual(front.preview_url, "https://example.com/p.png")


class DesignProjectDetailAndListPayloadTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "name": "Dragon Tee",
                "mockup_template_id": self.template.id,
                "placements": [
                    {"part_name": "front", "placement_override": {"x": 1, "y": 2, "width": 3, "height": 4}},
                    {"part_name": "back", "placement_override": {"x": 5, "y": 6, "width": 7, "height": 8}},
                ],
            },
            format="json",
        )
        self.project_id = response.data["id"]

    def test_detail_is_sufficient_to_reconstruct_all_parts(self):
        response = self.client.get(f"/api/generator/design-projects/{self.project_id}/")
        self.assertEqual(response.status_code, 200)
        data = response.data

        self.assertIn("mockup_template", data)
        self.assertIn("parts", data["mockup_template"])
        self.assertEqual(
            {p["name"] for p in data["mockup_template"]["parts"]}, {"front", "back"}
        )

        self.assertEqual(len(data["placements"]), 2)
        front = next(p for p in data["placements"] if p["part_name"] == "front")
        self.assertEqual(front["placement_override"], {"x": 1, "y": 2, "width": 3, "height": 4, "rotation": 0, "opacity": 1, "corner_radius": 0, "fit": "contain"})
        self.assertIn("crop_override", front)
        self.assertIn("text_elements", front)
        self.assertIn("template_part_id", front)

    def test_list_does_not_serialize_full_placement_collection(self):
        response = self.client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data if r["id"] == self.project_id)
        self.assertNotIn("placements", row)
        self.assertIn("placement_count", row)
        self.assertEqual(row["placement_count"], 2)

    def test_list_endpoint_query_count_does_not_scale_with_project_count(self):
        with CaptureQueriesContext(connection) as baseline:
            response = self.client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        baseline_query_count = len(baseline.captured_queries)

        for i in range(5):
            create_response = self.client.post(
                "/api/generator/design-projects/",
                {
                    "name": f"Extra {i}",
                    "mockup_template_id": self.template.id,
                    "placements": [
                        {"part_name": "front", "placement_override": {"x": 1, "y": 2, "width": 3, "height": 4}},
                        {"part_name": "back", "placement_override": {"x": 5, "y": 6, "width": 7, "height": 8}},
                    ],
                },
                format="json",
            )
            self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)

        with CaptureQueriesContext(connection) as scaled:
            response = self.client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)
        scaled_query_count = len(scaled.captured_queries)

        self.assertEqual(
            baseline_query_count,
            scaled_query_count,
            "List endpoint issued more queries as project count grew (N+1 regression): "
            f"{baseline_query_count} queries for 1 project vs {scaled_query_count} queries for 6 projects.",
        )


class DesignProjectUpdateTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "name": "Original",
                "mockup_template_id": self.template.id,
                "placements": [
                    {"part_name": "front", "placement_override": {"x": 1, "y": 1, "width": 10, "height": 10}},
                    {"part_name": "back", "placement_override": {"x": 2, "y": 2, "width": 20, "height": 20}},
                ],
            },
            format="json",
        )
        self.project_id = response.data["id"]

    def test_patch_updates_one_placement_without_deleting_others(self):
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/",
            {"placements": [{"part_name": "front", "placement_override": {"x": 99, "y": 99, "width": 10, "height": 10}}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(project.placements.count(), 2)
        self.assertEqual(project.placements.get(part_name="front").x, 99)
        self.assertEqual(project.placements.get(part_name="back").x, 2)

    def test_put_replaces_all_placements_and_deletes_omitted(self):
        response = self.client.put(
            f"/api/generator/design-projects/{self.project_id}/",
            {
                "mockup_template_id": self.template.id,
                "placements": [{"part_name": "front", "placement_override": {"x": 5, "y": 5, "width": 10, "height": 10}}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(list(project.placements.values_list("part_name", flat=True)), ["front"])

    def test_patch_can_add_a_new_placement(self):
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/",
            {"placements": [{"part_name": "front", "placement_override": {"x": 1, "y": 1, "width": 10, "height": 10}}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(project.placements.count(), 2)

    def test_rename_project(self):
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/", {"name": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DesignProject.objects.get(pk=self.project_id).name, "Renamed")

    def test_patch_name_only_does_not_remove_any_placement(self):
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/", {"name": "Renamed Again"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(
            set(project.placements.values_list("part_name", flat=True)), {"front", "back"}
        )

    def test_invalid_put_rolls_back_all_changes(self):
        response = self.client.put(
            f"/api/generator/design-projects/{self.project_id}/",
            {
                "name": "Should Not Apply",
                "mockup_template_id": self.template.id,
                "placements": [
                    {"part_name": "front", "placement_override": {"x": 1, "y": 1, "width": 10, "height": 10, "opacity": 5}},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(project.name, "Original")
        self.assertEqual(
            set(project.placements.values_list("part_name", flat=True)), {"front", "back"}
        )

    def test_change_variant_updates_color_and_size_snapshot(self):
        product = make_shop_product(self.template, slug="variant-product")
        variant = make_variant(self.template, product=product, color_name="Cyber White", size="XL")
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/",
            {"selected_variant_id": variant.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(project.selected_variant_id, variant.id)
        self.assertEqual(project.selected_color, "Cyber White")
        self.assertEqual(project.selected_size, "XL")

    def test_invalid_nested_update_leaves_existing_data_untouched(self):
        response = self.client.patch(
            f"/api/generator/design-projects/{self.project_id}/",
            {
                "name": "Should Not Apply",
                "placements": [
                    {"part_name": "front", "placement_override": {"x": 1, "y": 1, "width": 10, "height": 10, "opacity": 5}},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        project = DesignProject.objects.get(pk=self.project_id)
        self.assertEqual(project.name, "Original")
        self.assertEqual(project.placements.get(part_name="front").x, 1)


class DesignProjectValidationTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.template_a = make_template(slug="template-a")
        self.template_b = make_template(slug="template-b")
        self.product_a = make_shop_product(self.template_a, slug="product-a")
        self.product_b = make_shop_product(self.template_b, slug="product-b")
        self.client.force_authenticate(user=self.user)

    def test_rejects_variant_from_another_template(self):
        other_variant = make_variant(self.template_b, color_name="Black", size="M")
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": self.template_a.id, "selected_variant_id": other_variant.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("selected_variant_id", response.data)

    def test_rejects_variant_from_another_product(self):
        mismatched_variant = make_variant(self.template_a, product=self.product_b, color_name="Black", size="M")
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "product_id": self.product_a.id,
                "selected_variant_id": mismatched_variant.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("selected_variant_id", response.data)

    def test_rejects_unavailable_variant(self):
        variant = make_variant(self.template_a, is_available=False)
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": self.template_a.id, "selected_variant_id": variant.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_unsupported_part_for_template_without_parts(self):
        bare_template = make_template(slug="bare", with_parts=False)
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": bare_template.id, "placements": [{"part_name": "back"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("placements", response.data)

    def test_rejects_part_variant_does_not_support(self):
        variant = make_variant(self.template_a, supported_print_areas=["front"])
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "selected_variant_id": variant.id,
                "placements": [{"part_name": "back"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_generated_image_owned_by_another_user(self):
        other = make_user("owner2")
        image = GeneratedImage.objects.create(user=other, prompt="not yours")
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": self.template_a.id, "source_generated_image_id": image.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("source_generated_image_id", response.data)

    def test_rejects_invalid_opacity(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "placements": [{"part_name": "front", "placement_override": {"opacity": 1.5}}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_invalid_crop_dimensions(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "placements": [{"part_name": "front", "crop_override": {"width": 0, "height": 0}}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_unsupported_fit(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "placements": [{"part_name": "front", "placement_override": {"fit": "stretch"}}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_duplicate_part_name_in_same_request(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "placements": [{"part_name": "front"}, {"part_name": "front"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_allows_text_only_placement_with_no_image(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {
                "mockup_template_id": self.template_a.id,
                "placements": [{"part_name": "front", "text_elements": [{"text": "HELLO"}]}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_allows_intentionally_empty_placement(self):
        response = self.client.post(
            "/api/generator/design-projects/",
            {"mockup_template_id": self.template_a.id, "placements": [{"part_name": "front"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class DesignProjectDuplicateTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.template = make_template()
        self.client.force_authenticate(user=self.user)
        category = Category.objects.create(name="Cat", slug="cat")
        self.artwork = Artwork.objects.create(title="Art", slug="art", category=category)
        create_response = self.client.post(
            "/api/generator/design-projects/",
            {
                "name": "Dragon Tee",
                "mockup_template_id": self.template.id,
                "placements": [
                    {"part_name": "front", "source_artwork_id": self.artwork.id, "preview_url": "https://example.com/f.png"}
                ],
            },
            format="json",
        )
        self.original_id = create_response.data["id"]

    def test_duplicate_creates_new_project_and_placements_for_owner(self):
        response = self.client.post(f"/api/generator/design-projects/{self.original_id}/duplicate/")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotEqual(response.data["id"], self.original_id)

        duplicate = DesignProject.objects.get(pk=response.data["id"])
        original = DesignProject.objects.get(pk=self.original_id)
        self.assertEqual(duplicate.user_id, self.user.id)
        self.assertNotEqual(
            list(duplicate.placements.values_list("id", flat=True)),
            list(original.placements.values_list("id", flat=True)),
        )
        self.assertEqual(duplicate.placements.get(part_name="front").source_artwork_id, self.artwork.id)
        self.assertEqual(duplicate.placements.get(part_name="front").preview_url, "https://example.com/f.png")
        self.assertEqual(duplicate.status, DesignProject.Status.DRAFT)

    def test_duplicate_names_increment_and_stay_unique(self):
        first = self.client.post(f"/api/generator/design-projects/{self.original_id}/duplicate/").data
        second = self.client.post(f"/api/generator/design-projects/{self.original_id}/duplicate/").data
        third = self.client.post(f"/api/generator/design-projects/{self.original_id}/duplicate/").data
        self.assertEqual(first["name"], "Dragon Tee Copy")
        self.assertEqual(second["name"], "Dragon Tee Copy 2")
        self.assertEqual(third["name"], "Dragon Tee Copy 3")

    def test_duplicate_does_not_mutate_original(self):
        response = self.client.post(f"/api/generator/design-projects/{self.original_id}/duplicate/")
        duplicate_id = response.data["id"]
        self.client.patch(
            f"/api/generator/design-projects/{duplicate_id}/", {"name": "Changed Copy"}, format="json"
        )
        original = DesignProject.objects.get(pk=self.original_id)
        self.assertEqual(original.name, "Dragon Tee")


class ProductAndVariantAPITests(APITestCase):
    def setUp(self):
        self.template = make_template()

    def test_product_exposes_template_mapping(self):
        product = make_shop_product(self.template, slug="mapped")
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mockup_template_id"], self.template.id)

    def test_product_exposes_available_variants_and_sizes_colors(self):
        product = make_shop_product(self.template, slug="withvariants")
        make_variant(self.template, product=product, color_name="Black", size="M")
        make_variant(self.template, product=product, color_name="White", size="L", is_available=False)
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(len(response.data["variants"]), 1)
        self.assertEqual(response.data["available_sizes"], ["M"])
        self.assertEqual(response.data["available_colors"], ["Black"])

    def test_product_without_variants_falls_back_to_template_lists(self):
        template = make_template(slug="fallback-template", supported_colors=["Red"], supported_sizes=["S"])
        product = make_shop_product(template, slug="novariants")
        response = self.client.get(f"/api/shop/products/{product.slug}/")
        self.assertEqual(response.data["variants"], [])
        self.assertEqual(response.data["available_sizes"], ["S"])
        self.assertEqual(response.data["available_colors"], ["Red"])

    def test_variant_list_filters_by_product_and_template(self):
        product = make_shop_product(self.template, slug="filtertest")
        variant = make_variant(self.template, product=product, color_name="Black", size="M")
        response = self.client.get(f"/api/generator/product-variants/?product_id={product.id}")
        self.assertEqual([v["id"] for v in response.data], [variant.id])

        response = self.client.get(f"/api/generator/product-variants/?template_id={self.template.id}")
        self.assertEqual([v["id"] for v in response.data], [variant.id])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class DesignProjectThumbnailTests(TestCase):
    """Uses a temp MEDIA_ROOT: TestCase rolls back DB rows per test but not filesystem writes,
    so image uploads here would otherwise leave stray files behind in the real media/ folder."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template()

    def _project(self, **overrides):
        return DesignProject.objects.create(user=self.user, mockup_template=self.template, **overrides)

    def _resolved(self, project):
        from .serializers import resolve_display_thumbnail_url

        # Mirror the prefetch the list view applies, so this exercises the same code path.
        project = DesignProject.objects.select_related("mockup_template").prefetch_related("placements").get(
            pk=project.pk
        )
        return resolve_display_thumbnail_url(project)

    def test_uploaded_thumbnail_wins_over_everything(self):
        project = self._project(thumbnail_url="https://example.com/from-url.png")
        attach_image(project.thumbnail)
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="https://example.com/front.png")
        # Storage may suffix the filename to avoid a collision, so just check it's the
        # uploaded-thumbnail path (design-projects/thumbnails/...), not the thumbnail_url or
        # any placement preview.
        self.assertIn("design-projects/thumbnails/", self._resolved(project))

    def test_thumbnail_url_used_when_no_uploaded_thumbnail(self):
        project = self._project(thumbnail_url="https://example.com/from-url.png")
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="https://example.com/front.png")
        self.assertEqual(self._resolved(project), "https://example.com/from-url.png")

    def test_front_placement_preview_used_next(self):
        project = self._project()
        DesignPlacement.objects.create(design_project=project, part_name="back", preview_url="https://example.com/back.png")
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="https://example.com/front.png")
        self.assertEqual(self._resolved(project), "https://example.com/front.png")

    def test_front_preferred_over_back_explicitly(self):
        project = self._project()
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="https://example.com/front.png")
        DesignPlacement.objects.create(design_project=project, part_name="back", preview_url="https://example.com/back.png")
        self.assertEqual(self._resolved(project), "https://example.com/front.png")

    def test_first_non_front_preview_used_when_front_has_none(self):
        project = self._project()
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="")
        DesignPlacement.objects.create(design_project=project, part_name="back", preview_url="https://example.com/back.png")
        self.assertEqual(self._resolved(project), "https://example.com/back.png")

    def test_template_base_image_used_when_no_previews(self):
        attach_image(self.template.base_image, "template-base.png")
        project = self._project()
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="")
        self.assertIn("mockup-templates/base/", self._resolved(project))

    def test_empty_when_nothing_available(self):
        project = self._project()
        DesignPlacement.objects.create(design_project=project, part_name="front", preview_url="")
        self.assertEqual(self._resolved(project), "")

    def test_list_endpoint_exposes_both_thumbnail_fields(self):
        client_user = make_user("thumb_api_user")
        project = DesignProject.objects.create(user=client_user, mockup_template=self.template, thumbnail_url="https://example.com/x.png")
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=client_user)
        response = client.get("/api/generator/design-projects/")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data if r["id"] == project.id)
        self.assertIn("thumbnail_url", row)
        self.assertIn("display_thumbnail_url", row)
        self.assertEqual(row["display_thumbnail_url"], "https://example.com/x.png")


class ProductVariantUniquenessTests(TestCase):
    def setUp(self):
        self.template = make_template()

    def test_two_different_products_may_share_black_m_on_one_template(self):
        product_a = make_shop_product(self.template, slug="brand-a")
        product_b = make_shop_product(self.template, slug="brand-b")
        make_variant(self.template, product=product_a, color_name="Black", size="M")
        # Should not raise — different product, same template/colour/size.
        make_variant(self.template, product=product_b, color_name="Black", size="M")
        self.assertEqual(ProductVariant.objects.filter(color_name="Black", size="M").count(), 2)

    def test_same_template_colour_size_variants_remain_distinct_rows_per_product(self):
        product_a = make_shop_product(self.template, slug="brand-e")
        product_b = make_shop_product(self.template, slug="brand-f")
        variant_a = make_variant(self.template, product=product_a, color_name="Black", size="M")
        variant_b = make_variant(self.template, product=product_b, color_name="Black", size="M")

        # Distinct primary keys — these are two separate rows, not a deduplicated single variant.
        self.assertNotEqual(variant_a.id, variant_b.id)

        # Each row remains correctly and exclusively associated with its own product.
        refreshed_a = ProductVariant.objects.get(pk=variant_a.id)
        refreshed_b = ProductVariant.objects.get(pk=variant_b.id)
        self.assertEqual(refreshed_a.product_id, product_a.id)
        self.assertEqual(refreshed_b.product_id, product_b.id)
        self.assertNotEqual(refreshed_a.product_id, refreshed_b.product_id)

        # Fetching by product scopes to exactly one variant each, never both.
        self.assertEqual(list(ProductVariant.objects.filter(product=product_a).values_list("id", flat=True)), [variant_a.id])
        self.assertEqual(list(ProductVariant.objects.filter(product=product_b).values_list("id", flat=True)), [variant_b.id])

    def test_same_product_cannot_have_duplicate_black_m(self):
        product = make_shop_product(self.template, slug="brand-c")
        make_variant(self.template, product=product, color_name="Black", size="M")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_variant(self.template, product=product, color_name="Black", size="M")

    def test_template_only_variants_still_cannot_duplicate(self):
        make_variant(self.template, product=None, color_name="Black", size="M")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_variant(self.template, product=None, color_name="Black", size="M")

    def test_product_template_mismatch_still_rejected_by_model_clean(self):
        other_template = make_template(slug="other-template")
        product = make_shop_product(self.template, slug="brand-d")
        mismatched = ProductVariant(
            template=other_template, product=product, color_name="Black", size="M", retail_price="19.99"
        )
        with self.assertRaises(ValidationError):
            mismatched.full_clean()
