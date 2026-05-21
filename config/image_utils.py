from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def build_thumbnail_content(source_field, *, size=(480, 480), quality=82) -> tuple[str, ContentFile]:
    source_field.open("rb")
    try:
        image = Image.open(source_field)
        image = ImageOps.exif_transpose(image)

        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            background.paste(image.convert("RGBA"), mask=alpha)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        image.thumbnail(size, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6)
    finally:
        source_field.close()

    stem = Path(source_field.name).stem or "thumbnail"
    return f"{stem}.webp", ContentFile(buffer.getvalue())
