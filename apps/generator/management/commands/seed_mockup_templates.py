from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFilter

from apps.generator.models import MockupTemplate, MockupTemplatePart


CANVAS_SIZE = (1200, 1200)
SLEEVE_CANVAS_SIZE = (700, 700)


def _new_canvas(size=CANVAS_SIZE):
    return Image.new("RGBA", size, (12, 13, 18, 255))


def _save_image_to_field(instance, field_name: str, filename: str, image: Image.Image):
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    getattr(instance, field_name).save(filename, ContentFile(buffer.getvalue()), save=False)


def _add_background_glow(image: Image.Image, color: tuple[int, int, int, int], box: tuple[int, int, int, int]):
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse(box, fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    image.alpha_composite(glow)


def _shirt_shapes():
    body = (360, 220, 840, 1020)
    left_sleeve = [(360, 250), (250, 330), (245, 520), (360, 560)]
    right_sleeve = [(840, 250), (950, 330), (955, 520), (840, 560)]
    neck = (500, 220, 700, 350)
    return body, left_sleeve, right_sleeve, neck


def _hoodie_shapes():
    body = (340, 230, 860, 1040)
    left_sleeve = [(340, 280), (220, 390), (235, 620), (355, 590)]
    right_sleeve = [(860, 280), (980, 390), (965, 620), (845, 590)]
    hood_outer = [(430, 230), (520, 120), (680, 120), (770, 230), (700, 340), (500, 340)]
    hood_inner = [(500, 205), (560, 150), (640, 150), (700, 205), (650, 290), (550, 290)]
    pocket = (455, 690, 745, 840)
    return body, left_sleeve, right_sleeve, hood_outer, hood_inner, pocket


def build_tshirt_assets():
    base = _new_canvas()
    _add_background_glow(base, (0, 243, 255, 35), (180, 120, 980, 980))
    _add_background_glow(base, (188, 19, 254, 28), (260, 260, 1040, 1100))

    body, left_sleeve, right_sleeve, neck = _shirt_shapes()
    draw = ImageDraw.Draw(base)
    garment_color = (34, 37, 45, 255)
    garment_edge = (52, 56, 69, 255)

    draw.rounded_rectangle(body, radius=110, fill=garment_color, outline=garment_edge, width=4)
    draw.polygon(left_sleeve, fill=garment_color, outline=garment_edge)
    draw.polygon(right_sleeve, fill=garment_color, outline=garment_edge)
    draw.ellipse(neck, fill=(12, 13, 18, 255))
    draw.arc((455, 210, 745, 380), start=15, end=165, fill=(73, 79, 96, 255), width=7)

    mask = Image.new("L", CANVAS_SIZE, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((390, 310, 810, 740), radius=45, fill=255)

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((285, 180, 915, 1045), fill=(0, 0, 0, 70))
    shadow_draw.line((420, 300, 390, 760), fill=(0, 0, 0, 28), width=14)
    shadow_draw.line((780, 300, 810, 760), fill=(0, 0, 0, 28), width=14)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))

    highlight = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse((355, 240, 845, 840), fill=(255, 255, 255, 16))
    highlight_draw.line((500, 250, 445, 760), fill=(255, 255, 255, 34), width=8)
    highlight_draw.line((700, 250, 755, 760), fill=(255, 255, 255, 18), width=6)
    highlight = highlight.filter(ImageFilter.GaussianBlur(8))

    config = {
        "base_price": 29.99,
        "print_area": "Center Chest Print",
        "is_recommended": True,
        "placement": {
            "x": 390,
            "y": 310,
            "width": 420,
            "height": 430,
            "fit": "contain",
            "rotation": 0,
            "opacity": 0.96,
            "corner_radius": 18,
        },
    }

    return base, mask, shadow, highlight, config


def build_tshirt_back_assets():
    base = _new_canvas()
    _add_background_glow(base, (0, 243, 255, 35), (180, 120, 980, 980))
    _add_background_glow(base, (188, 19, 254, 28), (260, 260, 1040, 1100))

    body, left_sleeve, right_sleeve, _neck = _shirt_shapes()
    draw = ImageDraw.Draw(base)
    garment_color = (34, 37, 45, 255)
    garment_edge = (52, 56, 69, 255)

    draw.rounded_rectangle(body, radius=110, fill=garment_color, outline=garment_edge, width=4)
    draw.polygon(left_sleeve, fill=garment_color, outline=garment_edge)
    draw.polygon(right_sleeve, fill=garment_color, outline=garment_edge)
    # Shallow back neckline plus a faint center seam, instead of the front collar dip.
    draw.arc((530, 205, 670, 265), start=5, end=175, fill=(73, 79, 96, 255), width=6)
    draw.line((600, 262, 600, 985), fill=(24, 26, 32, 130), width=2)

    mask = Image.new("L", CANVAS_SIZE, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((390, 320, 810, 900), radius=45, fill=255)

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((285, 180, 915, 1045), fill=(0, 0, 0, 70))
    shadow_draw.line((420, 300, 390, 760), fill=(0, 0, 0, 28), width=14)
    shadow_draw.line((780, 300, 810, 760), fill=(0, 0, 0, 28), width=14)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))

    highlight = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse((355, 240, 845, 840), fill=(255, 255, 255, 12))
    highlight = highlight.filter(ImageFilter.GaussianBlur(8))

    config = {
        "placement": {
            "x": 390,
            "y": 340,
            "width": 420,
            "height": 480,
            "fit": "contain",
            "rotation": 0,
            "opacity": 0.96,
            "corner_radius": 18,
        },
    }

    return base, mask, shadow, highlight, config


def build_hoodie_assets():
    base = _new_canvas()
    _add_background_glow(base, (188, 19, 254, 40), (150, 120, 1040, 1040))
    _add_background_glow(base, (0, 243, 255, 24), (300, 260, 980, 1100))

    body, left_sleeve, right_sleeve, hood_outer, hood_inner, pocket = _hoodie_shapes()
    draw = ImageDraw.Draw(base)
    garment_color = (26, 27, 33, 255)
    garment_edge = (46, 48, 58, 255)

    draw.rounded_rectangle(body, radius=85, fill=garment_color, outline=garment_edge, width=4)
    draw.polygon(left_sleeve, fill=garment_color, outline=garment_edge)
    draw.polygon(right_sleeve, fill=garment_color, outline=garment_edge)
    draw.polygon(hood_outer, fill=(32, 34, 43, 255), outline=garment_edge)
    draw.polygon(hood_inner, fill=(15, 16, 22, 255))
    draw.rounded_rectangle(pocket, radius=28, outline=(70, 74, 88, 255), width=5)
    draw.line((520, 250, 520, 355), fill=(120, 124, 140, 255), width=5)
    draw.line((680, 250, 680, 355), fill=(120, 124, 140, 255), width=5)

    mask = Image.new("L", CANVAS_SIZE, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((360, 340, 840, 760), radius=50, fill=255)

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((250, 170, 950, 1060), fill=(0, 0, 0, 78))
    shadow_draw.line((430, 310, 380, 790), fill=(0, 0, 0, 30), width=15)
    shadow_draw.line((770, 310, 820, 790), fill=(0, 0, 0, 30), width=15)
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))

    highlight = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse((365, 245, 835, 845), fill=(255, 255, 255, 14))
    highlight_draw.line((510, 290, 455, 760), fill=(255, 255, 255, 28), width=8)
    highlight_draw.line((690, 290, 745, 760), fill=(255, 255, 255, 16), width=6)
    highlight = highlight.filter(ImageFilter.GaussianBlur(10))

    config = {
        "base_price": 49.99,
        "print_area": "Mid-Chest Hoodie Print",
        "is_recommended": True,
        "placement": {
            "x": 360,
            "y": 340,
            "width": 480,
            "height": 420,
            "fit": "contain",
            "rotation": 0,
            "opacity": 0.95,
            "corner_radius": 20,
        },
    }

    return base, mask, shadow, highlight, config


def build_hoodie_back_assets():
    base = _new_canvas()
    _add_background_glow(base, (188, 19, 254, 40), (150, 120, 1040, 1040))
    _add_background_glow(base, (0, 243, 255, 24), (300, 260, 980, 1100))

    body, left_sleeve, right_sleeve, _hood_outer, _hood_inner, _pocket = _hoodie_shapes()
    draw = ImageDraw.Draw(base)
    garment_color = (26, 27, 33, 255)
    garment_edge = (46, 48, 58, 255)

    draw.rounded_rectangle(body, radius=85, fill=garment_color, outline=garment_edge, width=4)
    draw.polygon(left_sleeve, fill=garment_color, outline=garment_edge)
    draw.polygon(right_sleeve, fill=garment_color, outline=garment_edge)
    # A raised hood silhouette peeking over the shoulders, plus a faint center seam.
    draw.arc((470, 190, 730, 330), start=190, end=350, fill=(46, 48, 58, 255), width=8)
    draw.line((600, 300, 600, 1000), fill=(15, 16, 22, 140), width=2)

    mask = Image.new("L", CANVAS_SIZE, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((360, 370, 840, 920), radius=50, fill=255)

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((250, 170, 950, 1060), fill=(0, 0, 0, 78))
    shadow_draw.line((430, 310, 380, 790), fill=(0, 0, 0, 30), width=15)
    shadow_draw.line((770, 310, 820, 790), fill=(0, 0, 0, 30), width=15)
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))

    highlight = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse((365, 245, 835, 845), fill=(255, 255, 255, 12))
    highlight = highlight.filter(ImageFilter.GaussianBlur(10))

    config = {
        "placement": {
            "x": 360,
            "y": 380,
            "width": 480,
            "height": 460,
            "fit": "contain",
            "rotation": 0,
            "opacity": 0.95,
            "corner_radius": 20,
        },
    }

    return base, mask, shadow, highlight, config


def _sleeve_assets(side: str, garment_color, garment_edge):
    """A close-up sleeve view, used for small left/right sleeve print placements."""
    width, height = SLEEVE_CANVAS_SIZE
    base = _new_canvas(SLEEVE_CANVAS_SIZE)
    _add_background_glow(base, (0, 243, 255, 30), (60, 60, width - 60, height - 60))

    draw = ImageDraw.Draw(base)
    # A rounded sleeve cap, mirrored for left vs right.
    sleeve_body = [
        (width * 0.5, height * 0.08),
        (width * 0.9, height * 0.22),
        (width * 0.82, height * 0.92),
        (width * 0.18, height * 0.92),
        (width * 0.1, height * 0.22),
    ]
    if side == "right_sleeve":
        sleeve_body = [(width - x, y) for x, y in sleeve_body]

    draw.polygon(sleeve_body, fill=garment_color, outline=garment_edge)
    cuff_y = height * 0.86
    draw.line((width * 0.2, cuff_y, width * 0.8, cuff_y), fill=garment_edge, width=5)

    mask = Image.new("L", SLEEVE_CANVAS_SIZE, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (width * 0.28, height * 0.28, width * 0.72, height * 0.7), radius=30, fill=255
    )

    shadow = Image.new("RGBA", SLEEVE_CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.polygon(sleeve_body, fill=(0, 0, 0, 40))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))

    highlight = Image.new("RGBA", SLEEVE_CANVAS_SIZE, (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_ellipse_box = (
        (width * 0.15, height * 0.1, width * 0.65, height * 0.6)
        if side == "left_sleeve"
        else (width * 0.35, height * 0.1, width * 0.85, height * 0.6)
    )
    highlight_draw.ellipse(highlight_ellipse_box, fill=(255, 255, 255, 18))
    highlight = highlight.filter(ImageFilter.GaussianBlur(10))

    config = {
        "placement": {
            "x": round(width * 0.3),
            "y": round(height * 0.32),
            "width": round(width * 0.4),
            "height": round(height * 0.35),
            "fit": "contain",
            "rotation": 0,
            "opacity": 0.95,
            "corner_radius": 10,
        },
    }

    return base, mask, shadow, highlight, config


def build_tshirt_sleeve_assets(side: str):
    return _sleeve_assets(side, garment_color=(34, 37, 45, 255), garment_edge=(52, 56, 69, 255))


def build_hoodie_sleeve_assets(side: str):
    return _sleeve_assets(side, garment_color=(26, 27, 33, 255), garment_edge=(46, 48, 58, 255))


class Command(BaseCommand):
    help = "Create starter T-shirt and hoodie mockup templates with generated local assets, including front/back/sleeve parts."

    def handle(self, *args, **options):
        template_specs = [
            {
                "name": "Starter T-Shirt Black",
                "slug": "starter-tshirt-black",
                "product_type": MockupTemplate.ProductType.TSHIRT,
                "description": "Starter black T-shirt template generated locally for merch preview setup.",
                "colors": ["Midnight Black", "Cyber White", "Ash Grey"],
                "sizes": ["S", "M", "L", "XL", "XXL"],
                "part_builders": {
                    MockupTemplatePart.PartName.FRONT: build_tshirt_assets,
                    MockupTemplatePart.PartName.BACK: build_tshirt_back_assets,
                    MockupTemplatePart.PartName.LEFT_SLEEVE: lambda: build_tshirt_sleeve_assets("left_sleeve"),
                    MockupTemplatePart.PartName.RIGHT_SLEEVE: lambda: build_tshirt_sleeve_assets("right_sleeve"),
                },
            },
            {
                "name": "Starter Hoodie Black",
                "slug": "starter-hoodie-black",
                "product_type": MockupTemplate.ProductType.HOODIE,
                "description": "Starter black hoodie template generated locally for merch preview setup.",
                "colors": ["Midnight Black", "Cyber White", "Heather Grey"],
                "sizes": ["S", "M", "L", "XL", "XXL"],
                "part_builders": {
                    MockupTemplatePart.PartName.FRONT: build_hoodie_assets,
                    MockupTemplatePart.PartName.BACK: build_hoodie_back_assets,
                    MockupTemplatePart.PartName.LEFT_SLEEVE: lambda: build_hoodie_sleeve_assets("left_sleeve"),
                    MockupTemplatePart.PartName.RIGHT_SLEEVE: lambda: build_hoodie_sleeve_assets("right_sleeve"),
                },
            },
        ]

        for spec in template_specs:
            template, created = MockupTemplate.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "product_type": spec["product_type"],
                    "description": spec["description"],
                },
            )

            # Root template-level images no longer exist (see CHANGELOG.md) — every layer lives
            # on a MockupTemplatePart now, including 'front', built below from the exact same
            # part_builders['front'] function that used to also populate the root fields.
            template.name = spec["name"]
            template.product_type = spec["product_type"]
            template.description = spec["description"]
            template.supported_colors = spec["colors"]
            template.supported_sizes = spec["sizes"]
            template.template_version = max(1, template.template_version)
            template.save()

            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} template '{template.name}' ({template.slug})."))

            for part_name, part_builder in spec["part_builders"].items():
                part_base, part_mask, part_shadow, part_highlight, part_config = part_builder()
                part, part_created = MockupTemplatePart.objects.get_or_create(
                    template=template,
                    name=part_name,
                )
                part.config = part_config
                _save_image_to_field(part, "base_image", f"{spec['slug']}-{part_name}-base.png", part_base)
                _save_image_to_field(part, "mask_image", f"{spec['slug']}-{part_name}-mask.png", part_mask.convert("RGBA"))
                _save_image_to_field(part, "shadow_layer", f"{spec['slug']}-{part_name}-shadow.png", part_shadow)
                _save_image_to_field(part, "highlight_layer", f"{spec['slug']}-{part_name}-highlight.png", part_highlight)
                part.save()

                part_action = "Created" if part_created else "Updated"
                self.stdout.write(
                    self.style.SUCCESS(f"  {part_action} part '{part_name}' for '{template.slug}'.")
                )

            # Now that every part exists, the template is actually usable — activate it (mirrors
            # AdminMockupTemplateSerializer.validate()'s "needs >= 1 part" rule).
            if not template.is_active:
                template.is_active = True
                template.save(update_fields=["is_active", "updated_at"])
