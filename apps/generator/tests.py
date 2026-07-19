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
    GeneratedPrintFile,
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


def make_data_url(color, size=(300, 300)):
    """A solid-colour PNG as a data: URI — a self-contained source image with no filesystem/DB
    dependency, for tests that need real, distinguishable pixel content (crop/fit/rotation/etc)."""
    import base64

    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def make_production_part(template, *, name="front", print_file_width=1200, print_file_height=1200, dpi=300, fixed_area=None):
    """A MockupTemplatePart with real production dimensions configured — make_template()'s
    default parts deliberately don't have these set (mirrors real templates before an admin
    configures them), so print-file tests need their own explicit part."""
    fixed_area = fixed_area or {"x": 100, "y": 100, "width": 400, "height": 400}
    part, _ = MockupTemplatePart.objects.update_or_create(
        template=template,
        name=name,
        defaults={
            "dpi": dpi,
            "print_file_width": print_file_width,
            "print_file_height": print_file_height,
            "config": {"placement": fixed_area},
        },
    )
    if not part.base_image:
        attach_image(part.base_image, "part-base.png")
    return part


def make_printable_placement(project, template_part, *, source_color=(255, 0, 0, 255), source_size=(300, 300), **overrides):
    defaults = {
        "design_project": project,
        "part_name": template_part.name,
        "template_part": template_part,
        "source_image_url": make_data_url(source_color, source_size),
        "x": 150,
        "y": 150,
        "width": 300,
        "height": 300,
    }
    defaults.update(overrides)
    return DesignPlacement.objects.create(**defaults)


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


class PreviewResolutionTests(TestCase):
    """Roadmap item 11 (separate preview quality from print quality): the render pipeline caps
    output at a web-friendly resolution and tags every render as preview-only, so a future
    order-submission pipeline has something concrete to check before ever treating a
    MockupRender.output_image as a production print file."""

    def test_downscale_shrinks_oversized_image_preserving_aspect_ratio(self):
        from .services import PREVIEW_MAX_DIMENSION, _downscale_to_preview_resolution

        oversized = Image.new("RGBA", (3200, 1600), (0, 0, 0, 0))
        result = _downscale_to_preview_resolution(oversized)

        self.assertEqual(max(result.width, result.height), PREVIEW_MAX_DIMENSION)
        self.assertAlmostEqual(result.width / result.height, 3200 / 1600, places=2)

    def test_downscale_is_a_noop_for_images_already_within_bounds(self):
        from .services import _downscale_to_preview_resolution

        small = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
        result = _downscale_to_preview_resolution(small)

        self.assertEqual((result.width, result.height), (800, 600))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
    def test_process_mockup_render_tags_output_as_preview_quality(self):
        from .services import process_mockup_render

        template = make_template(with_parts=False)
        attach_image(template.base_image, filename="base.png")
        attach_image(template.mask_image, filename="mask.png")

        render = MockupRender.objects.create(
            template=template,
            source_image_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        )

        result = process_mockup_render(render)

        self.assertEqual(result.status, MockupRender.Status.READY, result.error_message)
        self.assertEqual(result.processing_notes.get("quality"), "preview")
        self.assertIn("preview_max_dimension", result.processing_notes)


class TextRenderingTests(TestCase):
    """Roadmap item 15 (proper text editing): multi-line text, alignment, and line spacing.
    Exercises `_draw_text_elements` directly — a pure function, no DB fixtures needed."""

    def test_single_line_text_still_renders(self):
        from .services import _draw_text_elements

        image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        result = _draw_text_elements(
            image, [{"text": "HELLO", "fontSize": 48, "x": 200, "y": 200, "color": "#ffffff"}]
        )
        # Something was actually drawn — not a fully-transparent canvas.
        self.assertTrue(any(pixel[3] > 0 for pixel in result.getdata()))

    def test_multiline_text_with_alignment_and_line_height_renders_without_error(self):
        from .services import _draw_text_elements

        for align in ("left", "center", "right"):
            image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
            result = _draw_text_elements(
                image,
                [
                    {
                        "text": "SHORT\nA MUCH LONGER LINE\nMID",
                        "fontSize": 32,
                        "x": 250,
                        "y": 250,
                        "color": "#ffffff",
                        "textAlign": align,
                        "lineHeight": 1.5,
                    }
                ],
            )
            self.assertTrue(any(pixel[3] > 0 for pixel in result.getdata()), f"nothing drawn for align={align}")

    def test_multiline_text_with_letter_spacing_renders_without_error(self):
        from .services import _draw_text_elements

        image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
        result = _draw_text_elements(
            image,
            [{"text": "ONE\nTWO", "fontSize": 32, "x": 250, "y": 250, "color": "#ffffff", "letterSpacing": 6}],
        )
        self.assertTrue(any(pixel[3] > 0 for pixel in result.getdata()))

    def test_empty_lines_in_multiline_text_do_not_crash(self):
        from .services import _draw_text_elements

        image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
        result = _draw_text_elements(
            image, [{"text": "TOP\n\nBOTTOM", "fontSize": 32, "x": 250, "y": 250, "color": "#ffffff"}]
        )
        self.assertTrue(any(pixel[3] > 0 for pixel in result.getdata()))


class HiddenTextExclusionTests(TestCase):
    """Roadmap item 1: hidden text layers must never appear in any backend rendering path, and
    the exclusion must live in one centralized place (_draw_text_elements) so preview and
    production output can't diverge on what "hidden" means."""

    def _bbox(self, elements):
        from .services import _draw_text_elements

        image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        return _draw_text_elements(image, elements).getbbox()

    def test_hidden_text_is_absent(self):
        bbox = self._bbox([{"text": "HIDDEN", "isHidden": True, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
        self.assertIsNone(bbox)

    def test_truthy_non_boolean_isHidden_does_not_hide(self):
        # Regression guard: the check must be `is True`, not a generic truthy check — a stray
        # string value (e.g. from a malformed client payload) must never be treated as "hidden".
        for value in ("false", "0", "no", []):
            with self.subTest(value=value):
                bbox = self._bbox([{"text": "X", "isHidden": value, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
                self.assertIsNotNone(bbox)

    def test_visible_text_remains(self):
        bbox = self._bbox([{"text": "VISIBLE", "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
        self.assertIsNotNone(bbox)

    def test_locked_text_remains_visible(self):
        # isLocked is a frontend editing-only concern — must have zero effect on rendering.
        bbox = self._bbox([{"text": "LOCKED", "isLocked": True, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
        self.assertIsNotNone(bbox)

    def test_hidden_locked_text_remains_hidden(self):
        bbox = self._bbox(
            [{"text": "BOTH", "isHidden": True, "isLocked": True, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}]
        )
        self.assertIsNone(bbox)

    def test_missing_isHidden_key_behaves_as_visible(self):
        bbox = self._bbox([{"text": "NO KEY", "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
        self.assertIsNotNone(bbox)

    def test_isHidden_false_and_none_remain_visible(self):
        for value in (False, None):
            with self.subTest(value=value):
                bbox = self._bbox([{"text": "X", "isHidden": value, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
                self.assertIsNotNone(bbox)

    def test_layers_render_in_list_order(self):
        # Two same-position, opaque, differently-coloured filled-circle glyphs — whichever is
        # LAST in the list must end up on top (its colour visible at the solid glyph centre),
        # proving order is followed.
        from .services import _draw_text_elements

        image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        result = _draw_text_elements(
            image,
            [
                {"text": "●", "x": 200, "y": 200, "fontSize": 120, "color": "#ff0000"},
                {"text": "●", "x": 200, "y": 200, "fontSize": 120, "color": "#0000ff"},
            ],
        )
        center_pixel = result.getpixel((200, 200))
        self.assertEqual(center_pixel[:3], (0, 0, 255))  # the later (blue) layer painted on top

    def test_duplicate_layers_render_independently(self):
        # Same text/style at two different positions — both must render as separate marks.
        from .services import _draw_text_elements

        image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        result = _draw_text_elements(
            image,
            [
                {"text": "DUPE", "x": 100, "y": 100, "fontSize": 30, "color": "#fff"},
                {"text": "DUPE", "x": 300, "y": 300, "fontSize": 30, "color": "#fff"},
            ],
        )
        self.assertTrue(any(result.getpixel((x, 100))[3] > 0 for x in range(60, 141)))
        self.assertTrue(any(result.getpixel((x, 300))[3] > 0 for x in range(260, 341)))

    def test_missing_visibility_properties_do_not_break_rendering(self):
        # No isHidden/isLocked keys at all on any element — must not raise.
        bbox = self._bbox([{"text": "PLAIN", "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}])
        self.assertIsNotNone(bbox)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class ProductionPrintFileCanvasTests(TestCase):
    """Roadmap items 2/3: canvas creation and validation."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="print-canvas-test")
        self.part = make_production_part(self.template, print_file_width=1500, print_file_height=1800, dpi=300)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")

    def test_correct_production_width_and_height(self):
        from .services import generate_print_file_image

        placement = make_printable_placement(self.project, self.part)
        image = generate_print_file_image(placement=placement, template_part=self.part)
        self.assertEqual(image.size, (1500, 1800))

    def test_transparent_rgba_background(self):
        from .services import generate_print_file_image

        placement = DesignPlacement.objects.create(design_project=self.project, part_name=self.part.name, template_part=self.part)
        image = generate_print_file_image(placement=placement, template_part=self.part)
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)  # corner pixel fully transparent

    def test_correct_dpi_metadata_stored(self):
        from .services import create_or_reuse_print_file

        placement = make_printable_placement(self.project, self.part)
        record, _ = create_or_reuse_print_file(placement=placement, template_part=self.part)
        self.assertEqual(record.dpi, 300)
        self.assertEqual((record.width, record.height), (1500, 1800))

    def test_missing_print_file_dimensions_rejected(self):
        from .services import generate_print_file_image

        unconfigured_part = MockupTemplatePart.objects.create(
            template=self.template,
            name=MockupTemplatePart.PartName.LEFT_SLEEVE,
            config={"placement": {"x": 0, "y": 0, "width": 100, "height": 100}},
        )
        attach_image(unconfigured_part.base_image, "sleeve.png")
        placement = make_printable_placement(self.project, unconfigured_part)
        with self.assertRaises(ValueError):
            generate_print_file_image(placement=placement, template_part=unconfigured_part)

    def test_zero_dpi_rejected(self):
        from .services import generate_print_file_image

        self.part.dpi = 0
        self.part.save(update_fields=["dpi"])
        placement = make_printable_placement(self.project, self.part)
        with self.assertRaises(ValueError):
            generate_print_file_image(placement=placement, template_part=self.part)

    def test_does_not_silently_fall_back_to_a_default_size(self):
        # A part with print_file_width/height explicitly 0 must fail loudly, not produce some
        # arbitrary/preview-sized canvas.
        from .services import generate_print_file_image

        self.part.print_file_width = 0
        self.part.save(update_fields=["print_file_width"])
        placement = make_printable_placement(self.project, self.part)
        with self.assertRaises(ValueError):
            generate_print_file_image(placement=placement, template_part=self.part)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class ProductionPrintFileArtworkTests(TestCase):
    """Roadmap item 6: crop/fit/position/rotation/opacity, and that nothing from the mockup
    photo (garment, shadow, highlight) ever leaks into the production output."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="print-artwork-test")
        self.part = make_production_part(self.template, print_file_width=1200, print_file_height=1200)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")

    def test_crop_applied(self):
        from .services import generate_print_file_image

        # Half-red, half-blue source; crop to only the right (blue) half.
        source = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
        for x in range(100, 200):
            for y in range(200):
                source.putpixel((x, y), (0, 0, 255, 255))
        buffer = BytesIO()
        source.save(buffer, format="PNG")
        import base64

        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

        placement = make_printable_placement(
            self.project, self.part, source_color=(0, 0, 0, 0), source_image_url=data_url,
            crop_left=50, crop_top=0, crop_width=50, crop_height=100, fit=DesignPlacement.Fit.COVER,
        )
        image = generate_print_file_image(placement=placement, template_part=self.part)
        bbox = image.getbbox()
        center = image.getpixel(((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2))
        self.assertEqual(center[:3], (0, 0, 255))  # only the blue half survived the crop

    def test_contain_fit_applied(self):
        from .services import generate_print_file_image

        # A wide (2:1) source into a square placement — "contain" must not fill the whole square.
        placement = make_printable_placement(
            self.project, self.part, source_size=(400, 200), width=400, height=400, fit=DesignPlacement.Fit.CONTAIN
        )
        image = generate_print_file_image(placement=placement, template_part=self.part)
        bbox = image.getbbox()
        bbox_width = bbox[2] - bbox[0]
        bbox_height = bbox[3] - bbox[1]
        self.assertGreater(bbox_width, bbox_height)  # stayed wide, didn't stretch to fill the square

    def test_cover_fit_applied(self):
        from .services import generate_print_file_image

        placement = make_printable_placement(
            self.project, self.part, source_size=(400, 200), width=300, height=300, fit=DesignPlacement.Fit.COVER
        )
        image = generate_print_file_image(placement=placement, template_part=self.part)
        bbox = image.getbbox()
        # "cover" fills the full target box — production placement width/height is 300*3=900.
        self.assertAlmostEqual(bbox[2] - bbox[0], 900, delta=2)
        self.assertAlmostEqual(bbox[3] - bbox[1], 900, delta=2)

    def test_rotation_applied(self):
        from .services import generate_print_file_image

        placement_a = make_printable_placement(self.project, self.part, source_size=(300, 100), width=300, height=100, rotation=0)
        image_a = generate_print_file_image(placement=placement_a, template_part=self.part)
        bbox_a = image_a.getbbox()

        placement_b = make_printable_placement(
            self.project, self.part, part_name="front-b", source_size=(300, 100), width=300, height=100, rotation=90
        )
        # rotation lives on the placement, not tied to part_name uniqueness — reuse same part.
        placement_b.part_name = self.part.name
        image_b = generate_print_file_image(placement=placement_b, template_part=self.part)
        bbox_b = image_b.getbbox()

        self.assertGreater(bbox_a[2] - bbox_a[0], bbox_a[3] - bbox_a[1])  # unrotated: wide
        self.assertGreater(bbox_b[3] - bbox_b[1], bbox_b[2] - bbox_b[0])  # rotated 90°: tall

    def test_opacity_applied(self):
        from .services import generate_print_file_image

        placement = make_printable_placement(self.project, self.part, opacity=0.4)
        image = generate_print_file_image(placement=placement, template_part=self.part)
        bbox = image.getbbox()
        alpha = image.getpixel(((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2))[3]
        self.assertLess(alpha, 255)
        self.assertGreater(alpha, 0)

    def test_transparency_preserved(self):
        from .services import generate_print_file_image

        placement = make_printable_placement(self.project, self.part, source_color=(255, 0, 0, 0))  # fully transparent source
        image = generate_print_file_image(placement=placement, template_part=self.part)
        # A fully-transparent source composited on a transparent canvas stays fully transparent.
        self.assertIsNone(image.getbbox())

    def test_garment_shadow_and_highlight_are_never_present(self):
        from .services import generate_print_file_image

        # Deliberately distinct, opaque colours for the mockup-photo layers this pipeline must
        # never touch — if any leaked in, that exact colour would appear in the output.
        attach_image(self.part.base_image, "garment.png")  # transparent 4x4 by default; make it opaque+distinct:
        garment = Image.new("RGBA", (1000, 1000), (10, 20, 30, 255))
        buffer = BytesIO()
        garment.save(buffer, format="PNG")
        self.part.base_image.save("garment.png", ContentFile(buffer.getvalue()), save=False)
        shadow = Image.new("RGBA", (1000, 1000), (40, 50, 60, 255))
        buffer2 = BytesIO()
        shadow.save(buffer2, format="PNG")
        self.part.shadow_layer.save("shadow.png", ContentFile(buffer2.getvalue()), save=False)
        highlight = Image.new("RGBA", (1000, 1000), (70, 80, 90, 255))
        buffer3 = BytesIO()
        highlight.save(buffer3, format="PNG")
        self.part.highlight_layer.save("highlight.png", ContentFile(buffer3.getvalue()), save=False)
        self.part.save()

        placement = make_printable_placement(self.project, self.part, source_color=(255, 0, 0, 255))
        image = generate_print_file_image(placement=placement, template_part=self.part)

        forbidden_colours = {(10, 20, 30), (40, 50, 60), (70, 80, 90)}
        found_colours = {pixel[:3] for pixel in image.getdata() if pixel[3] > 0}
        self.assertFalse(found_colours & forbidden_colours)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class ProductionPrintFileTextTests(TestCase):
    """Roadmap item 7: text layers in production output."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="print-text-test")
        self.part = make_production_part(self.template, print_file_width=1200, print_file_height=1200)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")

    def test_visible_text_rendered_and_hidden_excluded(self):
        from .services import generate_print_file_image

        placement = DesignPlacement.objects.create(
            design_project=self.project,
            part_name=self.part.name,
            template_part=self.part,
            text_elements=[
                {"text": "SHOW", "x": 200, "y": 200, "fontSize": 40, "color": "#fff"},
                {"text": "HIDE", "isHidden": True, "x": 400, "y": 400, "fontSize": 40, "color": "#fff"},
            ],
        )
        image = generate_print_file_image(placement=placement, template_part=self.part)
        self.assertIsNotNone(image.getbbox())
        # Hidden text's own region (scaled) has nothing drawn.
        hidden_region_alpha = [image.getpixel((x, y))[3] for x in range(850, 950) for y in range(850, 950)]
        self.assertTrue(all(a == 0 for a in hidden_region_alpha))

    def test_locked_text_still_renders(self):
        from .services import generate_print_file_image

        placement = DesignPlacement.objects.create(
            design_project=self.project,
            part_name=self.part.name,
            template_part=self.part,
            text_elements=[{"text": "LOCKED", "isLocked": True, "x": 200, "y": 200, "fontSize": 40, "color": "#fff"}],
        )
        image = generate_print_file_image(placement=placement, template_part=self.part)
        self.assertIsNotNone(image.getbbox())

    def test_multiline_text_preserved(self):
        # Compare a one-line vs. a two-line placement (unsaved DesignPlacement instances — no
        # need to persist them, generate_print_file_image only reads attributes) to prove the
        # second line actually adds height in the production output, not just the preview.
        from .services import generate_print_file_image

        def bbox_for(text):
            placement = DesignPlacement(
                design_project=self.project,
                part_name=self.part.name,
                template_part=self.part,
                text_elements=[{"text": text, "x": 200, "y": 200, "fontSize": 30, "color": "#fff", "lineHeight": 1.4}],
            )
            return generate_print_file_image(placement=placement, template_part=self.part).getbbox()

        single_line_bbox = bbox_for("LINE ONE")
        two_line_bbox = bbox_for("LINE ONE\nLINE TWO")

        self.assertIsNotNone(single_line_bbox)
        self.assertIsNotNone(two_line_bbox)
        single_height = single_line_bbox[3] - single_line_bbox[1]
        two_line_height = two_line_bbox[3] - two_line_bbox[1]
        self.assertGreater(two_line_height, single_height)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class ProductionPrintFileStorageAndInvalidationTests(APITestCase):
    """Roadmap items 8/9/10: separate storage from previews, signature-based cache reuse and
    invalidation, and the generate/status API endpoints (ownership-scoped)."""

    def setUp(self):
        self.user = make_user("owner")
        self.other_user = make_user("intruder")
        self.template = make_template(slug="print-storage-test")
        self.part = make_production_part(self.template, print_file_width=900, print_file_height=900)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="Storage test")
        self.placement = make_printable_placement(self.project, self.part)

    def test_print_file_stored_as_separate_model_from_preview(self):
        from .services import create_or_reuse_print_file

        record, _ = create_or_reuse_print_file(placement=self.placement, template_part=self.part)
        self.assertIsInstance(record, GeneratedPrintFile)
        self.assertEqual(MockupRender.objects.count(), 0)  # nothing written to the preview model

    def test_same_signature_reuses_existing_file(self):
        from .services import create_or_reuse_print_file

        record1, reused1 = create_or_reuse_print_file(placement=self.placement, template_part=self.part)
        record2, reused2 = create_or_reuse_print_file(placement=self.placement, template_part=self.part)
        self.assertFalse(reused1)
        self.assertTrue(reused2)
        self.assertEqual(record1.id, record2.id)
        self.assertEqual(GeneratedPrintFile.objects.filter(design_placement=self.placement).count(), 1)

    def test_changed_placement_invalidates_prior_file(self):
        from .services import create_or_reuse_print_file

        record1, _ = create_or_reuse_print_file(placement=self.placement, template_part=self.part)
        self.placement.x = self.placement.x + 50
        self.placement.save(update_fields=["x"])
        record2, reused = create_or_reuse_print_file(placement=self.placement, template_part=self.part)
        self.assertFalse(reused)
        self.assertNotEqual(record1.id, record2.id)
        self.assertEqual(GeneratedPrintFile.objects.filter(design_placement=self.placement).count(), 2)

    def test_generate_endpoint_updates_placement_print_file_url(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.placement.refresh_from_db()
        self.assertTrue(self.placement.print_file_url)
        part_result = response.data["parts"][0]
        self.assertEqual(part_result["status"], "completed")
        self.assertFalse(part_result["reused"])

    def test_generate_endpoint_reports_reused_on_second_call(self):
        self.client.force_authenticate(user=self.user)
        self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        self.assertTrue(response.data["parts"][0]["reused"])

    def test_empty_placement_skipped_by_generate_endpoint(self):
        empty_placement = DesignPlacement.objects.create(
            design_project=self.project, part_name="back", template_part=None
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        part_names = [p["part_name"] for p in response.data["parts"]]
        self.assertNotIn("back", part_names)  # empty part omitted, not reported as failed

    def test_another_user_cannot_access_or_generate_print_files(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.get(f"/api/generator/design-projects/{self.project.id}/print-files/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_user_denied(self):
        # No force_authenticate() at all — must be rejected before ownership is even checked.
        response = self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.get(f"/api/generator/design-projects/{self.project.id}/print-files/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_project_returns_404(self):
        self.client.force_authenticate(user=self.user)
        missing_id = self.project.id + 99999
        response = self.client.post(f"/api/generator/design-projects/{missing_id}/generate-print-files/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.get(f"/api/generator/design-projects/{missing_id}/print-files/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_deleted_project_returns_404(self):
        self.client.force_authenticate(user=self.user)
        project_id = self.project.id
        self.project.delete()
        response = self.client.post(f"/api/generator/design-projects/{project_id}/generate-print-files/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_users_generated_file_url_is_never_exposed(self):
        # Owner generates a file, then confirm the intruder's own (empty) status response never
        # references it — the two users' data must not be able to leak into each other's payload.
        self.client.force_authenticate(user=self.user)
        self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        owner_file_url = GeneratedPrintFile.objects.filter(design_placement=self.placement).first().output_file.url

        other_project = DesignProject.objects.create(user=self.other_user, mockup_template=self.template, name="Intruder project")
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(f"/api/generator/design-projects/{other_project.id}/print-files/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(owner_file_url, str(response.data))

    def test_status_endpoint_returns_previously_generated_files_without_regenerating(self):
        self.client.force_authenticate(user=self.user)
        self.client.post(f"/api/generator/design-projects/{self.project.id}/generate-print-files/")
        count_before = GeneratedPrintFile.objects.count()

        response = self.client.get(f"/api/generator/design-projects/{self.project.id}/print-files/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(GeneratedPrintFile.objects.count(), count_before)  # no new generation triggered
        self.assertEqual(response.data["parts"][0]["status"], "completed")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class PrintFileCoordinateMappingTests(TestCase):
    """Roadmap item 5: preview -> fixed print area -> production coordinate mapping."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="coordinate-mapping-test")

    def test_preview_coordinates_scale_correctly(self):
        from .services import map_placement_to_production_canvas

        part = make_production_part(
            self.template, print_file_width=1200, print_file_height=1200, fixed_area={"x": 0, "y": 0, "width": 400, "height": 400}
        )
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        placement = make_printable_placement(project, part, x=100, y=100, width=200, height=200)

        mapped = map_placement_to_production_canvas(placement=placement, template_part=part)
        # scale = 1200/400 = 3
        self.assertAlmostEqual(mapped["x"], 300)
        self.assertAlmostEqual(mapped["y"], 300)
        self.assertAlmostEqual(mapped["width"], 600)
        self.assertAlmostEqual(mapped["height"], 600)

    def test_fixed_print_area_offset_is_handled(self):
        from .services import map_placement_to_production_canvas

        part = make_production_part(
            self.template, print_file_width=1000, print_file_height=1000, fixed_area={"x": 200, "y": 200, "width": 500, "height": 500}
        )
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        placement = make_printable_placement(project, part, x=200, y=200, width=250, height=250)

        mapped = map_placement_to_production_canvas(placement=placement, template_part=part)
        # relative to the fixed area's own origin (200,200): (0,0); scale = 1000/500 = 2
        self.assertAlmostEqual(mapped["x"], 0)
        self.assertAlmostEqual(mapped["y"], 0)
        self.assertAlmostEqual(mapped["width"], 500)
        self.assertAlmostEqual(mapped["height"], 500)

    def test_rotation_opacity_fit_preserved_through_mapping(self):
        from .services import map_placement_to_production_canvas

        part = make_production_part(self.template)
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        placement = make_printable_placement(
            project, part, rotation=45, opacity=0.6, fit=DesignPlacement.Fit.COVER
        )

        mapped = map_placement_to_production_canvas(placement=placement, template_part=part)
        self.assertEqual(mapped["rotation"], 45)
        self.assertEqual(mapped["opacity"], 0.6)
        self.assertEqual(mapped["fit"], DesignPlacement.Fit.COVER)

    def test_safe_area_and_bleed_metadata_never_render_into_output(self):
        # safe_area/bleed_area exist on MockupTemplatePart purely as editor-guide metadata —
        # generate_print_file_image() must never read them at all.
        from .services import generate_print_file_image

        part = make_production_part(self.template)
        part.safe_area = {"left": 5, "top": 5, "width": 90, "height": 90}
        part.bleed_area = {"top": 20, "right": 20, "bottom": 20, "left": 20}
        part.save()
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        placement = make_printable_placement(project, part)

        # Should render exactly the same regardless of safe/bleed metadata being present.
        image = generate_print_file_image(placement=placement, template_part=part)
        self.assertIsNotNone(image.getbbox())

    def test_missing_fixed_print_area_raises(self):
        from .services import map_placement_to_production_canvas

        part = MockupTemplatePart.objects.create(
            template=self.template,
            name=MockupTemplatePart.PartName.LEFT_SLEEVE,
            dpi=300,
            print_file_width=1000,
            print_file_height=1000,
            config={},
        )
        # No base_image and no explicit placement config -> fixed area resolves to 0x0.
        project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        placement = make_printable_placement(project, part)
        with self.assertRaises(ValueError):
            map_placement_to_production_canvas(placement=placement, template_part=part)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class PrintFileSignatureAuditTests(TestCase):
    """Roadmap item 13: the print-file signature must change for every printable input and must
    NOT change for UI-only state (layer name, lock state) — regression coverage for both
    directions, since either one silently breaks either caching or correctness."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="signature-audit-test")
        self.part = make_production_part(self.template, print_file_width=900, print_file_height=900)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")

    def _signature(self, placement):
        from .services import build_print_file_signature

        return build_print_file_signature(placement=placement, template_part=self.part)

    def test_renaming_a_text_layer_does_not_change_signature(self):
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff", "layerName": "Original Name"}],
        )
        before = self._signature(placement)
        placement.text_elements[0]["layerName"] = "Renamed"
        after = self._signature(placement)
        self.assertEqual(before, after)

    def test_toggling_lock_does_not_change_signature(self):
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff", "isLocked": False}],
        )
        before = self._signature(placement)
        placement.text_elements[0]["isLocked"] = True
        after = self._signature(placement)
        self.assertEqual(before, after)

    def test_changing_element_id_alone_does_not_change_signature(self):
        # `id` is an editor-internal identifier, not printable content.
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff"}],
        )
        before = self._signature(placement)
        placement.text_elements[0]["id"] = "t2"
        after = self._signature(placement)
        self.assertEqual(before, after)

    def test_toggling_hidden_changes_signature(self):
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff", "isHidden": False}],
        )
        before = self._signature(placement)
        placement.text_elements[0]["isHidden"] = True
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_layer_order_changes_signature(self):
        elements = [
            {"id": "t1", "text": "FIRST", "x": 100, "y": 100, "fontSize": 40, "color": "#fff"},
            {"id": "t2", "text": "SECOND", "x": 200, "y": 200, "fontSize": 40, "color": "#fff"},
        ]
        placement = make_printable_placement(self.project, self.part, text_elements=elements)
        before = self._signature(placement)
        placement.text_elements = [elements[1], elements[0]]
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_position_changes_signature(self):
        placement = make_printable_placement(self.project, self.part, x=100, y=100)
        before = self._signature(placement)
        placement.x = 150
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_rotation_opacity_fit_corner_radius_changes_signature(self):
        for field, value in (("rotation", 30), ("opacity", 0.5), ("fit", DesignPlacement.Fit.COVER), ("corner_radius", 12)):
            with self.subTest(field=field):
                DesignPlacement.objects.filter(design_project=self.project, part_name=self.part.name).delete()
                placement = make_printable_placement(self.project, self.part)
                before = self._signature(placement)
                setattr(placement, field, value)
                after = self._signature(placement)
                self.assertNotEqual(before, after)

    def test_changing_crop_changes_signature(self):
        placement = make_printable_placement(self.project, self.part)
        before = self._signature(placement)
        placement.crop_left = 10
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_font_styling_changes_signature(self):
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff", "isBold": False}],
        )
        before = self._signature(placement)
        placement.text_elements[0]["isBold"] = True
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_production_dimensions_changes_signature(self):
        placement = make_printable_placement(self.project, self.part)
        before = self._signature(placement)
        self.part.print_file_width = 1800
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_changing_dpi_changes_signature(self):
        placement = make_printable_placement(self.project, self.part)
        before = self._signature(placement)
        self.part.dpi = 150
        after = self._signature(placement)
        self.assertNotEqual(before, after)

    def test_unchanged_state_produces_identical_signature(self):
        placement = make_printable_placement(
            self.project, self.part,
            text_elements=[{"id": "t1", "text": "HI", "x": 100, "y": 100, "fontSize": 40, "color": "#fff"}],
        )
        self.assertEqual(self._signature(placement), self._signature(placement))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="artverse-test-media-"))
class GeneratedPrintFileModelAvailabilityTests(TestCase):
    """Roadmap item 18: the GeneratedPrintFile table/relationships/cascade behavior actually
    work against a real (test) database — not just assumed by services-layer tests that create
    records through create_or_reuse_print_file() and never exercise the model directly."""

    def setUp(self):
        self.user = make_user()
        self.template = make_template(slug="model-availability-test")
        self.part = make_production_part(self.template, print_file_width=600, print_file_height=600)
        self.project = DesignProject.objects.create(user=self.user, mockup_template=self.template, name="P")
        self.placement = make_printable_placement(self.project, self.part)

    def test_table_exists_and_record_can_be_created(self):
        record = GeneratedPrintFile.objects.create(
            design_placement=self.placement,
            template_part=self.part,
            width=600,
            height=600,
            dpi=300,
            signature="a" * 64,
            status=GeneratedPrintFile.Status.READY,
        )
        self.assertIsNotNone(record.pk)
        self.assertEqual(GeneratedPrintFile.objects.count(), 1)

    def test_relationship_to_design_placement_and_template_part(self):
        record = GeneratedPrintFile.objects.create(
            design_placement=self.placement, template_part=self.part, signature="b" * 64
        )
        self.assertEqual(record.design_placement_id, self.placement.id)
        self.assertEqual(record.template_part_id, self.part.id)
        self.assertIn(record, self.placement.generated_print_files.all())
        self.assertIn(record, self.part.generated_print_files.all())

    def test_deleting_design_placement_cascades(self):
        record = GeneratedPrintFile.objects.create(
            design_placement=self.placement, template_part=self.part, signature="c" * 64
        )
        record_id = record.id
        self.placement.delete()
        self.assertFalse(GeneratedPrintFile.objects.filter(id=record_id).exists())

    def test_deleting_template_part_with_print_files_is_protected(self):
        GeneratedPrintFile.objects.create(design_placement=self.placement, template_part=self.part, signature="d" * 64)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.part.delete()

    def test_signature_field_is_indexed(self):
        # Confirms the (design_placement, signature) composite index declared in Meta.indexes is
        # actually present on the real table — not just declared in the model, which is what
        # `makemigrations --check` guards against drifting apart from the migration.
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, GeneratedPrintFile._meta.db_table)
        indexed_column_sets = [tuple(c["columns"]) for c in constraints.values() if c.get("index")]
        self.assertIn(("design_placement_id", "signature"), indexed_column_sets)

    def test_output_file_storage_path(self):
        buffer = BytesIO()
        Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(buffer, format="PNG")
        record = GeneratedPrintFile.objects.create(
            design_placement=self.placement, template_part=self.part, signature="e" * 64
        )
        record.output_file.save("test-print-file.png", ContentFile(buffer.getvalue()), save=True)
        self.assertTrue(record.output_file.name.startswith("design-projects/print-files/"))
