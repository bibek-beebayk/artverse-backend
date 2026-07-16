from django.db import IntegrityError, transaction
from django.test import TestCase
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
