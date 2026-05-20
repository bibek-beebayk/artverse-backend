import hashlib
import json
import mimetypes
from pathlib import Path
from base64 import b64decode
from io import BytesIO
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageChops, ImageDraw, ImageOps

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
    variant_color: str = "",
    variant_size: str = "",
    placement_override: dict | None = None,
) -> str:
    normalized_override = json.dumps(placement_override or {}, sort_keys=True, separators=(",", ":"))
    raw_value = "|".join(
        [
            template.slug,
            str(template.template_version),
            source_fingerprint,
            variant_color.strip().lower(),
            variant_size.strip().lower(),
            normalized_override,
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


def _prepare_design_layer(source: Image.Image, placement: dict) -> Image.Image:
    width = max(1, int(placement.get("width", source.width)))
    height = max(1, int(placement.get("height", source.height)))
    fit_mode = str(placement.get("fit", "contain")).lower()

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


def render_mockup_to_image(render) -> Image.Image:
    template = render.template
    base_image = _load_storage_image(template.base_image)
    if base_image is None:
        raise ValueError("Mockup template base image is missing.")

    source_image = _load_source_image(render)
    config = template.config or {}
    placement = {
        **(config.get("placement") or {}),
        **_sanitize_placement_override(render.placement_override),
    }

    x = int(placement.get("x", 0) or 0)
    y = int(placement.get("y", 0) or 0)

    prepared_design = _prepare_design_layer(source_image, placement)
    design_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))

    target_width = max(1, int(placement.get("width", prepared_design.width) or prepared_design.width))
    target_height = max(1, int(placement.get("height", prepared_design.height) or prepared_design.height))
    paste_x = x + max(0, (target_width - prepared_design.width) // 2)
    paste_y = y + max(0, (target_height - prepared_design.height) // 2)
    design_layer.alpha_composite(prepared_design, dest=(paste_x, paste_y))

    mask_image = _load_storage_image(template.mask_image)
    design_layer = _apply_design_mask(design_layer, mask_image)

    composite = base_image.copy()
    composite.alpha_composite(design_layer)

    shadow_layer = _load_storage_image(template.shadow_layer)
    if shadow_layer is not None:
        composite.alpha_composite(shadow_layer.resize(base_image.size, Image.Resampling.LANCZOS))

    highlight_layer = _load_storage_image(template.highlight_layer)
    if highlight_layer is not None:
        composite.alpha_composite(highlight_layer.resize(base_image.size, Image.Resampling.LANCZOS))

    return composite


def process_mockup_render(render):
    render.status = render.Status.PROCESSING
    render.render_started_at = timezone.now()
    render.error_message = ""
    render.save(update_fields=["status", "render_started_at", "error_message", "updated_at"])

    try:
        output = render_mockup_to_image(render)
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
        }
        render.error_message = ""
    except Exception as exc:
        render.status = render.Status.FAILED
        render.error_message = str(exc)

    render.render_completed_at = timezone.now()
    render.save()
    return render
