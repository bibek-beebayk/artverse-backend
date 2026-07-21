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

from .models import DesignPlacement, GeneratedImage, GeneratedPrintFile, MockupTemplate, MockupTemplatePart, SourceDesignAsset


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


# Upload validation — device file uploads and client-generated AI images ("Upload Design" /
# "Generate with AI" in the customization action menu) share one entry point
# (create_uploaded_source_design_asset / SourceDesignAssetUploadView) since both are "here are
# raw image bytes I own, store them for me" — the only difference is the declared source_type.
ALLOWED_UPLOAD_CONTENT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
ALLOWED_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024  # 15MB
MIN_UPLOAD_DIMENSION_PX = 100  # below this, not a practical print-ready design


class UploadValidationError(ValueError):
    """Raised by create_uploaded_source_design_asset for any client-fixable upload problem —
    the upload view catches this specifically and returns 400 with the message, distinct from
    an unexpected server error."""


def create_uploaded_source_design_asset(
    *, uploaded_file, owner, source_type: str, title: str = ""
) -> SourceDesignAsset:
    """Validates and persists a user-supplied image as a PRIVATE SourceDesignAsset (owner set,
    never surfaced through any public listing endpoint). Deliberately distinct from
    ensure_source_design_asset()/hydrate_source_design_asset() above: those always re-encode to
    PNG for the mockup-render dedup cache; this preserves the ORIGINAL uploaded bytes and format
    untouched, per the "preserve original file" / "use the highest-quality source for production
    rendering" requirement for user-owned assets."""
    if source_type not in {SourceDesignAsset.SourceType.USER_UPLOAD, SourceDesignAsset.SourceType.AI_GENERATED}:
        raise UploadValidationError("Invalid source_type for an upload.")

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise UploadValidationError(f"Unsupported file type '{content_type or 'unknown'}'. Allowed: PNG, JPEG, WEBP.")

    extension = Path(uploaded_file.name or "").suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise UploadValidationError(f"Unsupported file extension '{extension or 'none'}'.")

    if uploaded_file.size <= 0:
        raise UploadValidationError("The uploaded file is empty.")
    if uploaded_file.size > MAX_UPLOAD_SIZE_BYTES:
        raise UploadValidationError(
            f"File is too large ({uploaded_file.size // (1024 * 1024)}MB). "
            f"Maximum is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB."
        )

    uploaded_file.seek(0)
    image_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    try:
        with Image.open(BytesIO(image_bytes)) as probe:
            probe.verify()
    except Exception as exc:
        raise UploadValidationError("This file could not be read as a valid image.") from exc

    # Re-open after verify() — per Pillow's docs, an Image object is unusable for anything else
    # once verify() has run, so this is a fresh decode from the same (immutable) bytes.
    with Image.open(BytesIO(image_bytes)) as decoded:
        width, height = decoded.size
        has_transparency = decoded.mode in {"RGBA", "LA"} or "transparency" in decoded.info

    if width < MIN_UPLOAD_DIMENSION_PX or height < MIN_UPLOAD_DIMENSION_PX:
        raise UploadValidationError(
            f"Image is too small ({width}x{height}px). Minimum is "
            f"{MIN_UPLOAD_DIMENSION_PX}x{MIN_UPLOAD_DIMENSION_PX}px."
        )

    # Owner-scoped fingerprint (not a bare content hash): SourceDesignAsset.source_fingerprint
    # is globally unique (shared with the gallery/URL dedup path above, which is genuinely
    # content-addressed). Two different users uploading byte-identical files must not collide on
    # that constraint — folding the owner id in keeps global uniqueness while still letting the
    # SAME user re-uploading identical bytes dedupe to their own existing asset.
    fingerprint = hashlib.sha256(f"user:{owner.pk}:".encode("utf-8") + image_bytes).hexdigest()

    existing = SourceDesignAsset.objects.filter(source_fingerprint=fingerprint).first()
    if existing and existing.image:
        return existing

    asset = SourceDesignAsset(
        owner=owner,
        source_type=source_type,
        title=title,
        source_fingerprint=fingerprint,
        width=width,
        height=height,
        mime_type=content_type,
        file_size=uploaded_file.size,
        has_transparency=has_transparency,
    )
    asset.image.save(f"{fingerprint}{extension}", ContentFile(image_bytes), save=False)
    asset.save()
    return asset


class AiGenerationError(RuntimeError):
    """Raised for any AI-generation-time problem (missing/invalid key, blocked or empty
    response, provider/network failure) — the view catches this specifically and marks the
    GenerationRequest failed instead of leaking a raw provider stack trace to the client."""


# Same wording as the client-side prompt this replaces (see CHANGELOG) — keeps generated output
# stylistically identical to what the direct-from-browser calls produced before this migration.
_PROMPT_PREFIX = "Digital art, cyberpunk style, neon lights, highly detailed, futuristic: "


def generate_ai_image(*, prompt: str, aspect_ratio: str) -> bytes:
    """Calls Gemini server-side and returns the generated image's raw bytes. The only AI-provider
    call in this codebase — apps.generator.views.GenerationRequestListCreateView is the only
    caller. Never called with a client-supplied API key; always apps.generator's own
    settings.GEMINI_API_KEY."""
    from django.conf import settings

    if not settings.GEMINI_ENABLED:
        raise AiGenerationError("AI generation is currently disabled.")
    if not settings.GEMINI_API_KEY:
        raise AiGenerationError("AI generation is not configured on the server.")

    from google import genai
    from google.genai import types
    from google.genai.errors import APIError

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL_NAME,
            contents=types.Content(parts=[types.Part.from_text(text=_PROMPT_PREFIX + prompt)]),
            config=types.GenerateContentConfig(image_config=types.ImageConfig(aspect_ratio=aspect_ratio)),
        )
    except APIError as exc:
        raise AiGenerationError(f"The AI provider request failed: {exc}") from exc
    except Exception as exc:
        raise AiGenerationError(f"The AI provider request failed: {exc}") from exc

    candidates = response.candidates or []
    for candidate in candidates:
        parts = candidate.content.parts if candidate.content else []
        for part in parts or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and inline_data.data:
                # The SDK already returns raw bytes here (unlike the JS SDK's base64 string) —
                # no further decoding needed.
                return inline_data.data

    raise AiGenerationError("The AI provider did not return an image for this prompt.")


def create_source_design_asset_from_generated_image(*, generated_image: GeneratedImage, owner) -> SourceDesignAsset:
    """Promotes an already-server-side GeneratedImage into a private SourceDesignAsset, reusing
    create_uploaded_source_design_asset's validation/dedup path instead of duplicating it — the
    only difference from a device upload is that the bytes already live in GeneratedImage.image
    rather than arriving in the request body."""
    if not generated_image.image:
        raise UploadValidationError("This generated image has no stored file to use.")

    from django.core.files.uploadedfile import SimpleUploadedFile

    generated_image.image.open("rb")
    try:
        image_bytes = generated_image.image.read()
    finally:
        generated_image.image.close()

    filename = Path(generated_image.image.name).name or f"generated-{generated_image.pk}.png"
    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    uploaded_file = SimpleUploadedFile(filename, image_bytes, content_type=content_type)

    return create_uploaded_source_design_asset(
        uploaded_file=uploaded_file,
        owner=owner,
        source_type=SourceDesignAsset.SourceType.AI_GENERATED,
        title=generated_image.prompt[:255],
    )


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
    """The single place that decides which text layers actually get drawn — every rendering
    path (preview renders, production print files, any future reusable text-compositing helper)
    must go through this function rather than filtering `isHidden` itself, so preview and
    production can never diverge on what "hidden" means. Layer order follows the input list
    order (callers are responsible for passing elements in the order they should be drawn/
    stacked); duplicate layers are independent dicts and render independently. `isLocked` is a
    frontend-only editing concern (prevents dragging in the browser) — it has no effect here."""
    if not text_elements:
        return image

    draw = ImageDraw.Draw(image)
    for elem in text_elements:
        # Explicitly truthy only — missing, False, or None all mean "visible" (the default).
        if elem.get("isHidden") is True:
            continue

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


# ---------------------------------------------------------------------------
# Production print-file generation.
#
# Coordinate convention (mirrors DesignProject's docstring, extended for production):
#   1. Preview/template-canvas coordinates — a DesignPlacement's x/y/width/height/rotation/etc,
#      in absolute pixels of the template part's base_image (the mockup photo). This is what the
#      editor's drag/resize/rotate UI manipulates and what gets persisted.
#   2. Fixed print-area coordinates — the SAME pixel space, but expressed relative to the
#      template part's admin-configured, non-draggable print boundary (`config["placement"]`,
#      see get_fixed_print_area()). The user's artwork transform is positioned *within or
#      relative to* this fixed area; the area itself is never user-editable.
#   3. Production print-file coordinates — the fixed print area scaled up to the part's required
#      print-file pixel dimensions (`print_file_width`/`print_file_height`) at its target `dpi`.
#      This is the only coordinate space a GeneratedPrintFile's canvas is ever drawn in.
#
# map_placement_to_production_canvas() is the one function that goes from (1) through (2) to (3)
# — every production render must go through it rather than re-deriving the scale math locally.
#
# Layer model (MVP, deliberate scope decision, not a gap to close later without a product
# decision to do so): a DesignPlacement has exactly one image layer (source_artwork /
# source_generated_image / source_image_url — a single artwork, never a stack) plus any number of
# independent text layers (`text_elements`). There is no unified image+text layer list and no
# multi-image-layer support here, by design — one artwork per print part, annotated with text.
# generate_print_file_image() reflects this directly: one artwork composite, then N text layers,
# never more than one image source. See PartCustomization's equivalent note on the frontend.
#
# Future work (not started, not scheduled): multiple image layers per part, and a unified
# image+text layer stack with real z-ordering (today the single image always composites first,
# text always on top — see the fixed draw order in generate_print_file_image/
# render_mockup_to_image). Would need a real schema change (a layer list with a discriminated
# type, not another field on DesignPlacement) plus matching editor/undo/signature work — a
# project-level decision, not a drive-by addition.
# ---------------------------------------------------------------------------


def get_fixed_print_area(template_part: MockupTemplatePart) -> dict:
    """The admin-configured, non-draggable print boundary for a template part — x/y/width/height
    in that part's base_image pixel space. Never user-editable; this is what production
    coordinates are computed relative to (see the module docstring above). Falls back to the
    full base_image extent if the part has no explicit `config["placement"]` set."""
    config = template_part.config or {}
    placement = config.get("placement") or {}
    image = _load_storage_image(template_part.base_image)
    fallback_width = image.width if image else 0
    fallback_height = image.height if image else 0
    return {
        "x": float(placement.get("x", 0) or 0),
        "y": float(placement.get("y", 0) or 0),
        "width": float(placement.get("width", fallback_width) or fallback_width),
        "height": float(placement.get("height", fallback_height) or fallback_height),
    }


def map_placement_to_production_canvas(*, placement: DesignPlacement, template_part: MockupTemplatePart) -> dict:
    """Stage (1) -> (2) -> (3) of the coordinate convention above, for one DesignPlacement's
    artwork transform. Returns a placement dict in production print-file pixel units, ready to
    hand to _prepare_design_layer() unchanged. All arithmetic is floating-point — callers round
    to int only at the final paste/canvas-size step, so repeated calls don't accumulate drift."""
    if not template_part.print_file_width or not template_part.print_file_height:
        raise ValueError(
            f"Template part '{template_part.name}' has no production print-file dimensions "
            "configured (print_file_width/print_file_height) — cannot generate a print file for it."
        )
    if template_part.print_file_width <= 0 or template_part.print_file_height <= 0:
        raise ValueError(f"Template part '{template_part.name}' has invalid production dimensions.")
    if not template_part.dpi or template_part.dpi <= 0:
        raise ValueError(f"Template part '{template_part.name}' has no valid required DPI configured.")

    fixed_area = get_fixed_print_area(template_part)
    if fixed_area["width"] <= 0 or fixed_area["height"] <= 0:
        raise ValueError(f"Template part '{template_part.name}' has no configured print-area boundary.")

    scale_x = template_part.print_file_width / fixed_area["width"]
    scale_y = template_part.print_file_height / fixed_area["height"]

    return {
        "x": (placement.x - fixed_area["x"]) * scale_x,
        "y": (placement.y - fixed_area["y"]) * scale_y,
        "width": placement.width * scale_x,
        "height": placement.height * scale_y,
        "rotation": placement.rotation,
        "opacity": placement.opacity,
        "fit": placement.fit,
        "corner_radius": placement.corner_radius * ((scale_x + scale_y) / 2),
    }


def is_placement_printable(placement: DesignPlacement) -> bool:
    """Does this placement have an actual artwork source configured? Mirrors the frontend's
    isPartConfigured() — text-only parts are still printable even with no artwork."""
    return bool(
        placement.source_artwork_id
        or placement.source_generated_image_id
        or (placement.source_image_url or "").strip()
    )


def _load_source_image_for_placement(placement: DesignPlacement) -> Image.Image:
    """Always the original, full-resolution source — nothing here is ever the downscaled
    preview output. `source_asset` (an uploaded file or a stored AI-generated result — see
    apps.generator.services.create_uploaded_source_design_asset) is checked FIRST: it's the
    highest-fidelity, explicitly-chosen source for a part customized via the editor's Upload or
    Generate-with-AI actions, stored as an untouched local file rather than a URL to re-fetch.
    A Gallery selection still resolves via `source_artwork` below, unchanged."""
    if placement.source_asset and placement.source_asset.image:
        image = _load_storage_image(placement.source_asset.image)
        if image is not None:
            return image

    if placement.source_generated_image:
        generated_image = placement.source_generated_image
        if generated_image.image:
            image = _load_storage_image(generated_image.image)
            if image is not None:
                return image
        if generated_image.image_url:
            return _load_remote_or_data_image(generated_image.image_url)

    if placement.source_artwork:
        artwork = placement.source_artwork
        if artwork.image:
            image = _load_storage_image(artwork.image)
            if image is not None:
                return image
        if artwork.image_url:
            return _load_remote_or_data_image(artwork.image_url)

    if placement.source_image_url:
        return _load_remote_or_data_image(placement.source_image_url)

    raise ValueError("No source image is available for this design placement.")


#: Keys `_draw_text_elements()` actually reads when rendering a text layer. Anything not in this
#: list is UI-only state (an editor identifier, a display label, a drag-lock flag) that has zero
#: effect on rendered output, and must therefore be excluded from the print-file signature — see
#: `_printable_text_element_state()` below. Keep this in sync with `_draw_text_elements()`'s own
#: `elem.get(...)` calls; a new printable text property needs to be added to *both*.
_PRINTABLE_TEXT_ELEMENT_KEYS = (
    "text",
    "fontFamily",
    "color",
    "fontSize",
    "x",
    "y",
    "rotation",
    "isBold",
    "isItalic",
    "letterSpacing",
    "textAlign",
    "lineHeight",
    "isHidden",
)


def _printable_text_element_state(text_elements: list) -> list:
    """Reduce each text element to only the fields that affect rendered output, preserving list
    order (order affects stacking, so it's part of the printable state too). Deliberately drops
    `id` (an editor-only identifier), `layerName` (a display label — renaming a layer must not
    invalidate its print file), and `isLocked` (prevents dragging in the browser only, per
    `_draw_text_elements()`'s own docstring — it never reaches the renderer)."""
    return [
        {key: element.get(key) for key in _PRINTABLE_TEXT_ELEMENT_KEYS}
        for element in (text_elements or [])
        if isinstance(element, dict)
    ]


def build_print_file_signature(*, placement: DesignPlacement, template_part: MockupTemplatePart) -> str:
    """A deterministic hash of every printable input for this placement + template part. Changing
    anything that would visibly change the output (source, crop, position, size, rotation,
    opacity, fit, corner radius, text content/styling/order/visibility, or the part's own
    production dimensions/DPI) changes this signature — an unchanged signature means an existing
    completed GeneratedPrintFile can be reused instead of regenerated. Non-printable UI state
    (layer names, lock state, which panel/guide is open) must never affect this — see
    `_printable_text_element_state()`."""
    source_fingerprint = resolve_source_fingerprint(
        generated_image=placement.source_generated_image,
        artwork=placement.source_artwork,
        source_image_url=placement.source_image_url,
    )
    printable_state = {
        "x": placement.x,
        "y": placement.y,
        "width": placement.width,
        "height": placement.height,
        "rotation": placement.rotation,
        "opacity": placement.opacity,
        "fit": placement.fit,
        "corner_radius": placement.corner_radius,
        "crop": [placement.crop_left, placement.crop_top, placement.crop_width, placement.crop_height],
        "text_elements": _printable_text_element_state(placement.text_elements),
    }
    raw_value = "|".join(
        [
            template_part.template.slug,
            str(template_part.template.template_version),
            template_part.name,
            str(template_part.print_file_width),
            str(template_part.print_file_height),
            str(template_part.dpi),
            source_fingerprint,
            json.dumps(printable_state, sort_keys=True, separators=(",", ":")),
        ]
    )
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def _scale_text_elements_to_production(text_elements: list, *, template_part: MockupTemplatePart) -> list:
    """Text coordinates/sizing need the same preview-canvas -> fixed-print-area -> production
    scaling as the artwork (see the module docstring). Visibility filtering itself happens once,
    centrally, inside _draw_text_elements() — this only rescales, it doesn't filter."""
    fixed_area = get_fixed_print_area(template_part)
    scale_x = template_part.print_file_width / fixed_area["width"]
    scale_y = template_part.print_file_height / fixed_area["height"]
    average_scale = (scale_x + scale_y) / 2

    scaled = []
    for element in text_elements or []:
        next_element = dict(element)
        next_element["x"] = (float(element.get("x", 0) or 0) - fixed_area["x"]) * scale_x
        next_element["y"] = (float(element.get("y", 0) or 0) - fixed_area["y"]) * scale_y
        next_element["fontSize"] = float(element.get("fontSize", 48) or 48) * average_scale
        next_element["letterSpacing"] = float(element.get("letterSpacing", 0) or 0) * average_scale
        scaled.append(next_element)
    return scaled


def generate_print_file_image(*, placement: DesignPlacement, template_part: MockupTemplatePart) -> Image.Image:
    """Pure compositing, no persistence: a transparent RGBA canvas at the template part's
    production dimensions, with only the customer's artwork (cropped/fit/positioned/rotated/
    opacity-adjusted, from the original source) and visible text layers drawn onto it. Nothing
    else — no garment/mockup photo, no mask, no displacement warp, no shadow/highlight, no
    safe-area or bleed guides. Raises ValueError (not silently falling back to preview
    dimensions) if the part has no valid production dimensions/DPI configured."""
    if not template_part.print_file_width or not template_part.print_file_height:
        raise ValueError(
            f"Template part '{template_part.name}' has no production print-file dimensions configured."
        )
    if template_part.print_file_width <= 0 or template_part.print_file_height <= 0:
        raise ValueError(f"Template part '{template_part.name}' has invalid production dimensions.")
    if not template_part.dpi or template_part.dpi <= 0:
        raise ValueError(f"Template part '{template_part.name}' has no valid required DPI configured.")

    canvas = Image.new("RGBA", (template_part.print_file_width, template_part.print_file_height), (0, 0, 0, 0))

    if is_placement_printable(placement):
        production_placement = map_placement_to_production_canvas(placement=placement, template_part=template_part)
        source_image = _load_source_image_for_placement(placement)
        crop_override = {
            "left": placement.crop_left,
            "top": placement.crop_top,
            "width": placement.crop_width,
            "height": placement.crop_height,
        }
        prepared = _prepare_design_layer(source_image, production_placement, crop_override)

        target_width = max(1, int(round(production_placement["width"])))
        target_height = max(1, int(round(production_placement["height"])))
        paste_x = int(round(production_placement["x"])) + max(0, (target_width - prepared.width) // 2)
        paste_y = int(round(production_placement["y"])) + max(0, (target_height - prepared.height) // 2)
        canvas.alpha_composite(prepared, dest=(paste_x, paste_y))

    if placement.text_elements:
        scaled_text_elements = _scale_text_elements_to_production(placement.text_elements, template_part=template_part)
        canvas = _draw_text_elements(canvas, scaled_text_elements)

    return canvas


def create_or_reuse_print_file(
    *, placement: DesignPlacement, template_part: MockupTemplatePart
) -> tuple[GeneratedPrintFile, bool]:
    """Orchestrates one part's print-file generation: computes the signature, reuses an existing
    completed file with the same signature if one exists, otherwise generates, persists, and
    returns a new GeneratedPrintFile (marking it FAILED with a safe error message on any error
    rather than leaving nothing behind or raising past the caller — the API view surfaces
    per-part status, not a single all-or-nothing exception). Returns (record, reused)."""
    signature = build_print_file_signature(placement=placement, template_part=template_part)

    existing = (
        GeneratedPrintFile.objects.filter(
            design_placement=placement, signature=signature, status=GeneratedPrintFile.Status.READY
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing, True

    record = GeneratedPrintFile.objects.create(
        design_placement=placement,
        template_part=template_part,
        signature=signature,
        status=GeneratedPrintFile.Status.PROCESSING,
        width=template_part.print_file_width or 0,
        height=template_part.print_file_height or 0,
        dpi=template_part.dpi or 0,
    )
    try:
        image = generate_print_file_image(placement=placement, template_part=template_part)
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        filename = f"{signature}.png"
        record.output_file.save(filename, ContentFile(buffer.getvalue()), save=False)
        record.width = image.width
        record.height = image.height
        record.status = GeneratedPrintFile.Status.READY
        record.error_message = ""
    except Exception as exc:
        record.status = GeneratedPrintFile.Status.FAILED
        record.error_message = str(exc)

    record.save()
    return record, False
