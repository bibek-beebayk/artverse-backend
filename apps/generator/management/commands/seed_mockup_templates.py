from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFilter

from apps.generator.models import MockupTemplate


CANVAS_SIZE = (1200, 1200)


def _new_canvas():
    return Image.new("RGBA", CANVAS_SIZE, (12, 13, 18, 255))


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


class Command(BaseCommand):
    help = "Create starter T-shirt and hoodie mockup templates with generated local assets."

    def handle(self, *args, **options):
        template_specs = [
            {
                "name": "Starter T-Shirt Black",
                "slug": "starter-tshirt-black",
                "product_type": MockupTemplate.ProductType.TSHIRT,
                "description": "Starter black T-shirt template generated locally for merch preview setup.",
                "colors": ["Midnight Black", "Cyber White", "Ash Grey"],
                "sizes": ["S", "M", "L", "XL", "XXL"],
                "builder": build_tshirt_assets,
            },
            {
                "name": "Starter Hoodie Black",
                "slug": "starter-hoodie-black",
                "product_type": MockupTemplate.ProductType.HOODIE,
                "description": "Starter black hoodie template generated locally for merch preview setup.",
                "colors": ["Midnight Black", "Cyber White", "Heather Grey"],
                "sizes": ["S", "M", "L", "XL", "XXL"],
                "builder": build_hoodie_assets,
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

            base, mask, shadow, highlight, config = spec["builder"]()
            template.name = spec["name"]
            template.product_type = spec["product_type"]
            template.description = spec["description"]
            template.is_active = True
            template.config = config
            template.supported_colors = spec["colors"]
            template.supported_sizes = spec["sizes"]
            template.template_version = max(1, template.template_version)

            _save_image_to_field(template, "base_image", f"{spec['slug']}-base.png", base)
            _save_image_to_field(template, "mask_image", f"{spec['slug']}-mask.png", mask.convert("RGBA"))
            _save_image_to_field(template, "shadow_layer", f"{spec['slug']}-shadow.png", shadow)
            _save_image_to_field(template, "highlight_layer", f"{spec['slug']}-highlight.png", highlight)
            template.save()

            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} template '{template.name}' ({template.slug})."))
