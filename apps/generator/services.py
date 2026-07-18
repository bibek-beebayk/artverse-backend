import hashlib
import json
import mimetypes
from pathlib import Path
from base64 import b64decode
from io import BytesIO
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageFont

from apps.gallery.models import Artwork

from .models import GeneratedImage, MockupTemplate, SourceDesignAsset


def resolve_source_fingerprint(
    *,
    generated_image: GeneratedImage | None = None,
    artwork: Artwork | None = None,
    source_image_url: str = "",
) -> str:
    if generated_image is not None:
        if generated_image.image:
            source = f"generated-image:{generated_image.pk}:{generated_image.image.name}"
        elif generated_image.image_url:
            source = f"generated-image-url:{generated_image.pk}:{generated_image.image_url}"
        else:
            source = f"generated-image:{generated_image.pk}:empty"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    if artwork is not None:
        if artwork.image:
            source = f"artwork-image:{artwork.pk}:{artwork.image.name}:{artwork.updated_at.isoformat()}"
        elif artwork.image_url:
            source = f"artwork-image-url:{artwork.pk}:{artwork.image_url}:{artwork.updated_at.isoformat()}"
        else:
            source = f"artwork:{artwork.pk}:empty"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    normalized = source_image_url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_source_asset_fingerprint(
    *,
    artwork: Artwork | None = None,
    source_image_url: str = "",
    uploaded_image=None,
    existing_fingerprint: str = "",
) -> str:
    if existing_fingerprint.strip():
        return existing_fingerprint.strip()

    if artwork is not None or source_image_url.strip():
        return resolve_source_fingerprint(artwork=artwork, source_image_url=source_image_url)

    if uploaded_image:
        if hasattr(uploaded_image, "open"):
            uploaded_image.open("rb")
        try:
            image_bytes = uploaded_image.read()
        finally:
            if hasattr(uploaded_image, "seek"):
                uploaded_image.seek(0)
        return hashlib.sha256(image_bytes).hexdigest()

    return ""


def build_mockup_cache_key(
    *,
    template: MockupTemplate,
    source_fingerprint: str,
    part_name: str = "",
    variant_color: str = "",
    variant_size: str = "",
    placement_override: dict | None = None,
    crop_override: dict | None = None,
) -> str:
    normalized_override = json.dumps(placement_override or {}, sort_keys=True, separators=(",", ":"))
    normalized_crop = json.dumps(crop_override or {}, sort_keys=True, separators=(",", ":"))
    raw_value = "|".join(
        [
            template.slug,
            str(template.template_version),
            part_name.strip().lower(),
            source_fingerprint,
            variant_color.strip().lower(),
            variant_size.strip().lower(),
            normalized_override,
            normalized_crop,
        ]
    )
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def _sanitize_placement_override(placement_override: dict | None) -> dict:
    if not isinstance(placement_override, dict):
        return {}

    normalized = {}
    for key in ("x", "y", "width", "height", "rotation", "corner_radius"):
        value = placement_override.get(key)
        if value is None or value == "":
            continue
        normalized[key] = int(float(value))

    opacity = placement_override.get("opacity")
    if opacity is not None and opacity != "":
        normalized["opacity"] = float(opacity)

    fit = placement_override.get("fit")
    if isinstance(fit, str) and fit.strip():
        normalized["fit"] = fit.strip().lower()

    return normalized


def _sanitize_crop_override(crop_override: dict | None) -> dict:
    if not isinstance(crop_override, dict):
        return {}

    normalized = {}
    for key in ("left", "top", "width", "height"):
        value = crop_override.get(key)
        if value is None or value == "":
            continue
        normalized[key] = float(value)

    width = max(5.0, min(100.0, normalized.get("width", 100.0)))
    height = max(5.0, min(100.0, normalized.get("height", 100.0)))
    left = normalized.get("left", 0.0)
    top = normalized.get("top", 0.0)

    left = max(0.0, min(100.0 - width, left))
    top = max(0.0, min(100.0 - height, top))

    normalized["left"] = left
    normalized["top"] = top
    normalized["width"] = width
    normalized["height"] = height

    return normalized


def _load_storage_image(file_field) -> Image.Image | None:
    if not file_field:
        return None

    file_field.open("rb")
    try:
        return Image.open(file_field).convert("RGBA")
    finally:
        file_field.close()


def _load_remote_or_data_image(url: str) -> Image.Image:
    image_bytes, _, _ = _read_remote_or_data_image(url)
    return Image.open(BytesIO(image_bytes)).convert("RGBA")


def _read_remote_or_data_image(url: str) -> tuple[bytes, str, str]:
    normalized = url.strip()
    if normalized.startswith("data:image/"):
        header, encoded = normalized.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[-1].strip().lower()
        extension = mimetypes.guess_extension(mime_type) or ".png"
        return b64decode(encoded), mime_type, extension

    request = Request(
        normalized,
        headers={"User-Agent": "ArtverseMockupRenderer/1.0"},
    )
    with urlopen(request, timeout=20) as response:
        mime_type = response.headers.get_content_type() or ""
        extension = mimetypes.guess_extension(mime_type) or Path(normalized).suffix or ".png"
        return response.read(), mime_type, extension


def _normalize_image_bytes(image_bytes: bytes) -> tuple[bytes, int, int]:
    with Image.open(BytesIO(image_bytes)) as source_image:
        normalized_image = source_image.convert("RGBA")
        width, height = normalized_image.size
        buffer = BytesIO()
        normalized_image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), width, height


def _resolve_source_design_bytes(
    *,
    source_image_url: str = "",
    artwork: Artwork | None = None,
) -> tuple[bytes, str, dict]:
    image_bytes = b""
    resolved_source_url = source_image_url.strip()
    notes = {}

    if artwork is not None and artwork.image:
        artwork.image.open("rb")
        try:
            image_bytes = artwork.image.read()
        finally:
            artwork.image.close()
        resolved_source_url = resolved_source_url or artwork.image.name
        notes["source_kind"] = "artwork-image"
        return image_bytes, resolved_source_url, notes

    if artwork is not None and artwork.image_url and not resolved_source_url:
        resolved_source_url = artwork.image_url.strip()
        notes["source_kind"] = "artwork-image-url"
    elif resolved_source_url.startswith("data:image/"):
        notes["source_kind"] = "data-url"
    elif resolved_source_url:
        notes["source_kind"] = "remote-url"

    if resolved_source_url:
        image_bytes, mime_type, extension = _read_remote_or_data_image(resolved_source_url)
        notes["mime_type"] = mime_type
        notes["extension"] = extension

    return image_bytes, resolved_source_url, notes


def hydrate_source_design_asset(
    asset: SourceDesignAsset,
    *,
    source_image_url: str = "",
    artwork: Artwork | None = None,
    title: str = "",
) -> SourceDesignAsset | None:
    image_bytes, resolved_source_url, notes = _resolve_source_design_bytes(
        source_image_url=source_image_url,
        artwork=artwork,
    )
    if not image_bytes:
        return None

    normalized_bytes, width, height = _normalize_image_bytes(image_bytes)
    asset.artwork = artwork
    asset.title = title or (artwork.title if artwork is not None else "")
    asset.source_url = "" if resolved_source_url.startswith("data:image/") else resolved_source_url
    asset.width = width
    asset.height = height
    asset.notes = notes
    asset.source_fingerprint = resolve_source_asset_fingerprint(
        artwork=artwork,
        source_image_url=source_image_url,
        existing_fingerprint=asset.source_fingerprint,
    )
    asset.image.save(f"{asset.source_fingerprint}.png", ContentFile(normalized_bytes), save=False)
    return asset


def ensure_source_design_asset(
    *,
    source_fingerprint: str,
    source_image_url: str = "",
    artwork: Artwork | None = None,
    title: str = "",
) -> SourceDesignAsset | None:
    asset = SourceDesignAsset.objects.filter(source_fingerprint=source_fingerprint).first()
    if asset and asset.image:
        needs_update = False
        if artwork and asset.artwork_id != artwork.pk:
            asset.artwork = artwork
            needs_update = True
        if title and asset.title != title:
            asset.title = title
            needs_update = True
        if not asset.source_url and source_image_url and not source_image_url.startswith("data:image/"):
            asset.source_url = source_image_url
            needs_update = True
        if needs_update:
            asset.save(update_fields=["artwork", "title", "source_url", "updated_at"])
        return asset

    asset = asset or SourceDesignAsset(source_fingerprint=source_fingerprint)
    asset = hydrate_source_design_asset(
        asset,
        source_image_url=source_image_url,
        artwork=artwork,
        title=title,
    )
    if asset is None:
        return None
    asset.save()
    return asset


def _load_source_image(render) -> Image.Image:
    if render.generated_image:
        generated_image = render.generated_image
        if generated_image.image:
            image = _load_storage_image(generated_image.image)
            if image is not None:
                return image
        if generated_image.image_url:
            return _load_remote_or_data_image(generated_image.image_url)

    if render.artwork:
        artwork = render.artwork
        if artwork.image:
            image = _load_storage_image(artwork.image)
            if image is not None:
                return image

    if render.source_asset:
        image = _load_storage_image(render.source_asset.image)
        if image is not None:
            return image

    if render.source_image_url:
        return _load_remote_or_data_image(render.source_image_url)

    raise ValueError("No source image is available for this mockup render.")


def _apply_opacity(image: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1:
        return image

    alpha = image.getchannel("A")
    alpha = alpha.point(lambda value: int(value * max(0.0, min(opacity, 1.0))))
    image.putalpha(alpha)
    return image


def _apply_corner_radius(image: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return image

    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, image.width, image.height), radius=radius, fill=255)
    image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return image


def _prepare_design_layer(source: Image.Image, placement: dict, crop_override: dict | None = None) -> Image.Image:
    width = max(1, int(placement.get("width", source.width)))
    height = max(1, int(placement.get("height", source.height)))
    fit_mode = str(placement.get("fit", "contain")).lower()
    normalized_crop = _sanitize_crop_override(crop_override)
    
    if normalized_crop and (
        normalized_crop.get("left", 0.0) != 0.0
        or normalized_crop.get("top", 0.0) != 0.0
        or normalized_crop.get("width", 100.0) != 100.0
        or normalized_crop.get("height", 100.0) != 100.0
    ):
        crop_left = int(round((normalized_crop["left"] / 100.0) * source.width))
        crop_top = int(round((normalized_crop["top"] / 100.0) * source.height))
        crop_right = int(round(((normalized_crop["left"] + normalized_crop["width"]) / 100.0) * source.width))
        crop_bottom = int(round(((normalized_crop["top"] + normalized_crop["height"]) / 100.0) * source.height))
        crop_right = max(crop_left + 1, min(source.width, crop_right))
        crop_bottom = max(crop_top + 1, min(source.height, crop_bottom))
        source = source.crop((crop_left, crop_top, crop_right, crop_bottom))
        fit_mode = "contain"

    if fit_mode == "cover":
        prepared = ImageOps.fit(
            source,
            (width, height),
            method=Image.Resampling.LANCZOS,
        )
    else:
        resized = source.copy()
        resized.thumbnail((width, height), Image.Resampling.LANCZOS)
        prepared = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        prepared.alpha_composite(
            resized,
            dest=((width - resized.width) // 2, (height - resized.height) // 2),
        )

    prepared = _apply_corner_radius(
        prepared,
        int(placement.get("corner_radius", 0) or 0),
    )
    prepared = _apply_opacity(
        prepared,
        float(placement.get("opacity", 1) or 1),
    )

    rotation = float(placement.get("rotation", 0) or 0)
    if rotation:
        prepared = prepared.rotate(
            rotation,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )

    return prepared


def _apply_design_mask(layer: Image.Image, mask_image: Image.Image | None) -> Image.Image:
    if mask_image is None:
        return layer

    normalized_mask = mask_image.convert("L").resize(layer.size, Image.Resampling.LANCZOS)
    alpha = layer.getchannel("A")
    layer.putalpha(ImageChops.multiply(alpha, normalized_mask))
    return layer


def _sample_bilinear_rgba(source_pixels, width: int, height: int, x: float, y: float) -> tuple[int, int, int, int]:
    x = max(0.0, min(width - 1, x))
    y = max(0.0, min(height - 1, y))

    x0 = int(x)
    y0 = int(y)
    x1 = min(x0 + 1, width - 1)
    y1 = min(y0 + 1, height - 1)
    tx = x - x0
    ty = y - y0

    c00 = source_pixels[x0, y0]
    c10 = source_pixels[x1, y0]
    c01 = source_pixels[x0, y1]
    c11 = source_pixels[x1, y1]

    result = []
    for index in range(4):
        top = c00[index] * (1.0 - tx) + c10[index] * tx
        bottom = c01[index] * (1.0 - tx) + c11[index] * tx
        value = top * (1.0 - ty) + bottom * ty
        result.append(int(round(value)))

    return tuple(result)


def _warp_rgba_with_displacement(
    layer_crop: Image.Image,
    displacement_crop: Image.Image,
    *,
    strength_x: float,
    strength_y: float,
    blur_radius: float,
) -> Image.Image:
    if strength_x <= 0 and strength_y <= 0:
        return layer_crop

    width, height = layer_crop.size
    if width <= 1 or height <= 1:
        return layer_crop

    normalized_map = displacement_crop.convert("L")
    if blur_radius > 0:
        normalized_map = normalized_map.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    source_pixels = layer_crop.load()
    map_pixels = normalized_map.load()
    warped = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    warped_pixels = warped.load()

    for y in range(height):
        for x in range(width):
            left = map_pixels[max(0, x - 1), y]
            right = map_pixels[min(width - 1, x + 1), y]
            up = map_pixels[x, max(0, y - 1)]
            down = map_pixels[x, min(height - 1, y + 1)]

            gradient_x = (right - left) / 255.0
            gradient_y = (down - up) / 255.0

            sample_x = x - (gradient_x * strength_x)
            sample_y = y - (gradient_y * strength_y)
            warped_pixels[x, y] = _sample_bilinear_rgba(source_pixels, width, height, sample_x, sample_y)

    return warped


def _apply_displacement_map(
    design_layer: Image.Image,
    displacement_map: Image.Image | None,
    config: dict | None = None,
) -> Image.Image:
    if displacement_map is None:
        return design_layer

    alpha_bbox = design_layer.getchannel("A").getbbox()
    if alpha_bbox is None:
        return design_layer

    displacement_config = (config or {}).get("displacement") or {}
    strength_x = float(displacement_config.get("strength_x", 12) or 0)
    strength_y = float(displacement_config.get("strength_y", 8) or 0)
    blur_radius = float(displacement_config.get("blur_radius", 1.4) or 0)

    if strength_x <= 0 and strength_y <= 0:
        return design_layer

    normalized_map = displacement_map.convert("L").resize(design_layer.size, Image.Resampling.LANCZOS)
    padding = max(2, int(round(max(strength_x, strength_y))) + 2)
    left = max(0, alpha_bbox[0] - padding)
    top = max(0, alpha_bbox[1] - padding)
    right = min(design_layer.width, alpha_bbox[2] + padding)
    bottom = min(design_layer.height, alpha_bbox[3] + padding)

    layer_crop = design_layer.crop((left, top, right, bottom))
    displacement_crop = normalized_map.crop((left, top, right, bottom))
    warped_crop = _warp_rgba_with_displacement(
        layer_crop,
        displacement_crop,
        strength_x=strength_x,
        strength_y=strength_y,
        blur_radius=blur_radius,
    )

    result = Image.new("RGBA", design_layer.size, (0, 0, 0, 0))
    result.alpha_composite(warped_crop, dest=(left, top))
    return result

import os

def _draw_text_elements(image: Image.Image, text_elements: list) -> Image.Image:
    if not text_elements:
        return image
    
    draw = ImageDraw.Draw(image)
    for elem in text_elements:
        text = str(elem.get("text", ""))
        if not text:
            continue
            
        color = str(elem.get("color", "#FFFFFF"))
        font_size = int(elem.get("fontSize", 48))
        font_family = str(elem.get("fontFamily", "Roboto"))
        x = float(elem.get("x", 0))
        y = float(elem.get("y", 0))
        rotation = float(elem.get("rotation", 0))
        is_bold = bool(elem.get("isBold", False))
        is_italic = bool(elem.get("isItalic", False))
        
        font_path = os.path.join(settings.BASE_DIR, "fonts", f"{font_family}.ttf")
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            try:
                font = ImageFont.truetype(os.path.join(settings.BASE_DIR, "fonts", "Roboto.ttf"), font_size)
            except OSError:
                font = ImageFont.load_default()
                
        stroke_width = max(1, font_size // 25) if is_bold else 0
        letter_spacing = float(elem.get("letterSpacing", 0))
        text_align = str(elem.get("textAlign", "center")).lower()
        if text_align not in {"left", "center", "right"}:
            text_align = "center"
        line_height_multiplier = float(elem.get("lineHeight") or 1.2)

        if "\n" not in text:
            # Single-line path — unchanged from before multi-line/alignment support was added,
            # so existing renders keep pixel-identical output regardless of the new fields above.
            if letter_spacing == 0:
                left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
                text_width = right - left
                text_height = bottom - top

                # Add extra padding for italic skew
                padding = 10 + (int(text_height * 0.3) if is_italic else 0)
                txt_layer = Image.new("RGBA", (int(text_width) + padding*2, int(text_height) + padding*2), (0,0,0,0))
                txt_draw = ImageDraw.Draw(txt_layer)
                txt_draw.text((-left + padding, -top + padding), text, font=font, fill=color, stroke_width=stroke_width, stroke_fill=color if is_bold else None)
            else:
                # Measure max height
                left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
                text_height = bottom - top

                total_width = sum(draw.textlength(c, font=font) for c in text) + letter_spacing * max(0, len(text) - 1)
                padding = 10 + (int(text_height * 0.3) if is_italic else 0)
                txt_layer = Image.new("RGBA", (int(total_width) + padding*2, int(text_height) + padding*2), (0,0,0,0))
                txt_draw = ImageDraw.Draw(txt_layer)

                current_x = padding
                for char in text:
                    c_left, c_top, c_right, c_bottom = draw.textbbox((0, 0), char, font=font, stroke_width=stroke_width)
                    txt_draw.text((current_x - c_left, -top + padding), char, font=font, fill=color, stroke_width=stroke_width, stroke_fill=color if is_bold else None)
                    current_x += draw.textlength(char, font=font) + letter_spacing
        else:
            # Multi-line path: each line keeps the same per-character/letter-spacing logic as
            # the single-line path above, laid out top-to-bottom at `lineHeight` * font-metric
            # spacing and aligned left/center/right within the widest line.
            lines = text.split("\n")

            def _line_width(line: str) -> float:
                if not line:
                    return 0.0
                if letter_spacing == 0:
                    l, _, r, _ = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
                    return r - l
                return sum(draw.textlength(c, font=font) for c in line) + letter_spacing * max(0, len(line) - 1)

            _, ref_top, _, ref_bottom = draw.textbbox((0, 0), "Ag", font=font, stroke_width=stroke_width)
            line_step = (ref_bottom - ref_top) * line_height_multiplier

            line_widths = [_line_width(line) for line in lines]
            max_width = max(line_widths) if line_widths else 0
            text_height = (ref_bottom - ref_top) + line_step * max(0, len(lines) - 1)

            padding = 10 + (int(text_height * 0.3) if is_italic else 0)
            txt_layer = Image.new("RGBA", (int(max_width) + padding*2, int(text_height) + padding*2), (0,0,0,0))
            txt_draw = ImageDraw.Draw(txt_layer)

            for index, line in enumerate(lines):
                if not line:
                    continue
                line_left, line_top, _, _ = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
                if text_align == "left":
                    x_offset = 0.0
                elif text_align == "right":
                    x_offset = max_width - line_widths[index]
                else:
                    x_offset = (max_width - line_widths[index]) / 2
                line_y = padding + index * line_step - line_top

                if letter_spacing == 0:
                    txt_draw.text((padding + x_offset - line_left, line_y), line, font=font, fill=color, stroke_width=stroke_width, stroke_fill=color if is_bold else None)
                else:
                    current_x = padding + x_offset
                    for char in line:
                        c_left, _, _, _ = draw.textbbox((0, 0), char, font=font, stroke_width=stroke_width)
                        txt_draw.text((current_x - c_left, line_y), char, font=font, fill=color, stroke_width=stroke_width, stroke_fill=color if is_bold else None)
                        current_x += draw.textlength(char, font=font) + letter_spacing

        if is_italic:
            # Skew transform matrix for italic: (1, 0.3, 0, 0, 1, 0)
            txt_layer = txt_layer.transform(
                (txt_layer.width + int(text_height * 0.3), txt_layer.height),
                Image.AFFINE,
                (1, 0.3, 0, 0, 1, 0),
                resample=Image.Resampling.BICUBIC
            )

        if rotation != 0:
            txt_layer = txt_layer.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
            
        paste_x = int(x - txt_layer.width / 2)
        paste_y = int(y - txt_layer.height / 2)
        
        image.alpha_composite(txt_layer, dest=(paste_x, paste_y))
        
    return image


def render_mockup_to_image(render) -> Image.Image:
    template = render.template
    part = None
    if render.part_name:
        part = template.parts.filter(name=render.part_name).first()

    base_image_source = part.base_image if part else template.base_image
    
    base_image = _load_storage_image(base_image_source)
    if base_image is None:
        raise ValueError(f"Mockup template base image is missing for {template.name} ({render.part_name or 'root'}).")

    source_image = _load_source_image(render)
    config = part.config if part else template.config
    config = config or {}
    placement = {
        **(config.get("placement") or {}),
        **_sanitize_placement_override(render.placement_override),
    }
    crop_override = _sanitize_crop_override(render.crop_override)

    x = int(placement.get("x", 0) or 0)
    y = int(placement.get("y", 0) or 0)

    prepared_design = _prepare_design_layer(source_image, placement, crop_override)
    design_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))

    target_width = max(1, int(placement.get("width", prepared_design.width) or prepared_design.width))
    target_height = max(1, int(placement.get("height", prepared_design.height) or prepared_design.height))
    paste_x = x + max(0, (target_width - prepared_design.width) // 2)
    paste_y = y + max(0, (target_height - prepared_design.height) // 2)
    design_layer.alpha_composite(prepared_design, dest=(paste_x, paste_y))

    design_layer = _draw_text_elements(design_layer, render.text_elements)

    mask_image_source = part.mask_image if part else template.mask_image
    displacement_map_source = part.displacement_map if part else template.displacement_map
    
    mask_image = _load_storage_image(mask_image_source)
    displacement_map = _load_storage_image(displacement_map_source)
    design_layer = _apply_displacement_map(design_layer, displacement_map, config)
    design_layer = _apply_design_mask(design_layer, mask_image)

    composite = base_image.copy()
    composite.alpha_composite(design_layer)

    shadow_layer_source = part.shadow_layer if part else template.shadow_layer
    shadow_layer = _load_storage_image(shadow_layer_source)
    if shadow_layer is not None:
        composite.alpha_composite(shadow_layer.resize(base_image.size, Image.Resampling.LANCZOS))

    highlight_layer_source = part.highlight_layer if part else template.highlight_layer
    highlight_layer = _load_storage_image(highlight_layer_source)
    if highlight_layer is not None:
        composite.alpha_composite(highlight_layer.resize(base_image.size, Image.Resampling.LANCZOS))

    return composite


# This pipeline (render_mockup_to_image / process_mockup_render) produces PREVIEW-QUALITY
# output only: a mockup photo with the design composited on top, capped at a web-friendly
# resolution. It intentionally does NOT produce a production print file — no transparent
# background, no full print DPI, no garment removed — because that's a separate, not-yet-built
# pipeline (roadmap Section 6, "Generate final print files"). `MockupRender.output_image` must
# never be submitted to Printify as the actual print file once that pipeline exists; the
# `processing_notes["quality"] = "preview"` tag below exists specifically so that future
# order-submission code has something concrete to assert against rather than relying on nobody
# ever wiring the wrong field into an order by mistake.
PREVIEW_MAX_DIMENSION = 1600


def _downscale_to_preview_resolution(image: Image.Image) -> Image.Image:
    """Cap the longest edge at PREVIEW_MAX_DIMENSION, preserving aspect ratio. A no-op if the
    image is already smaller — this only ever shrinks, never upscales, a render."""
    longest_edge = max(image.width, image.height)
    if longest_edge <= PREVIEW_MAX_DIMENSION:
        return image
    scale = PREVIEW_MAX_DIMENSION / longest_edge
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def process_mockup_render(render):
    render.status = render.Status.PROCESSING
    render.render_started_at = timezone.now()
    render.error_message = ""
    render.save(update_fields=["status", "render_started_at", "error_message", "updated_at"])

    try:
        output = render_mockup_to_image(render)
        output = _downscale_to_preview_resolution(output)
        buffer = BytesIO()
        output.save(buffer, format="PNG", optimize=True)
        filename = f"{render.cache_key}.png"
        render.output_image.save(filename, ContentFile(buffer.getvalue()), save=False)
        # Use the file-backed URL at serialization time instead of persisting
        # long signed storage URLs into the database.
        render.output_image_url = ""
        render.status = render.Status.READY
        render.processing_notes = {
            **(render.processing_notes or {}),
            "engine": "pillow-compositor-v1",
            "quality": "preview",
            "preview_max_dimension": PREVIEW_MAX_DIMENSION,
        }
        render.error_message = ""
    except Exception as exc:
        render.status = render.Status.FAILED
        render.error_message = str(exc)

    render.render_completed_at = timezone.now()
    render.save()
    return render
