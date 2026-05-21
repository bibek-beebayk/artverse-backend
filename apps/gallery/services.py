import csv
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from io import TextIOWrapper
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.db import transaction
from django.utils.text import slugify

from .models import Artwork, Category


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_filename(name: str) -> str:
    return Path(name).name.strip().lower()


@dataclass
class ArtworkImportRowResult:
    row_number: int
    slug: str
    action: str
    message: str


@dataclass
class ArtworkImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    row_results: list[ArtworkImportRowResult] = field(default_factory=list)


class ArtworkBulkImporter:
    expected_headers = {
        "title",
        "slug",
        "category",
        "description",
        "image_filename",
        "is_featured",
        "is_published",
        "image_url",
    }

    def __init__(
        self,
        *,
        update_existing: bool = True,
        auto_create_categories: bool = True,
        dry_run: bool = False,
    ):
        self.update_existing = update_existing
        self.auto_create_categories = auto_create_categories
        self.dry_run = dry_run

    def import_from_files(self, *, csv_file, images_zip_file=None) -> ArtworkImportResult:
        result = ArtworkImportResult()
        zip_context = self._open_zip(images_zip_file) if images_zip_file else nullcontext((None, {}))

        with zip_context as (archive, zip_index):
            csv_file.seek(0)
            wrapper = TextIOWrapper(csv_file, encoding="utf-8-sig", newline="")
            try:
                reader = csv.DictReader(wrapper)
                headers = set(reader.fieldnames or [])
                missing_headers = {"title", "category"} - headers
                if missing_headers:
                    raise ValueError(
                        "CSV is missing required headers: " + ", ".join(sorted(missing_headers))
                    )

                for row_number, row in enumerate(reader, start=2):
                    self._process_row(
                        row_number=row_number,
                        row=row,
                        archive=archive,
                        zip_index=zip_index,
                        result=result,
                    )
            finally:
                wrapper.detach()

        return result

    @contextmanager
    def _open_zip(self, images_zip_file):
        images_zip_file.seek(0)
        try:
            with ZipFile(images_zip_file) as archive:
                yield archive, {
                    _normalize_filename(name): name
                    for name in archive.namelist()
                    if not name.endswith("/")
                }
        except BadZipFile as exc:
            raise ValueError("The uploaded images ZIP file is invalid.") from exc

    def _read_zip_member(self, archive: ZipFile | None, zip_member_name: str | None) -> bytes | None:
        if archive is None or not zip_member_name:
            return None

        with archive.open(zip_member_name) as member:
            return member.read()

    def _resolve_category(self, category_name: str) -> tuple[Category | None, bool, str | None]:
        category_slug = slugify(category_name)

        category = Category.objects.filter(name__iexact=category_name).first()
        if category is not None:
            return category, False, None

        category = Category.objects.filter(slug=category_slug).first()
        if category is not None:
            return (
                category,
                False,
                f'Reused existing category "{category.name}" from matching slug "{category_slug}".',
            )

        if not self.auto_create_categories:
            return None, False, f'Category "{category_name}" does not exist.'

        if self.dry_run:
            return Category(name=category_name, slug=category_slug), True, None

        try:
            return Category.objects.create(name=category_name, slug=category_slug), True, None
        except IntegrityError:
            category = Category.objects.filter(slug=category_slug).first()
            if category is not None:
                return (
                    category,
                    False,
                    f'Reused existing category "{category.name}" after detecting slug collision.',
                )
            raise

    def _process_row(
        self,
        *,
        row_number: int,
        row: dict,
        archive: ZipFile | None,
        zip_index: dict[str, str],
        result: ArtworkImportResult,
    ) -> None:
        title = (row.get("title") or "").strip()
        if not title:
            result.failed += 1
            result.row_results.append(
                ArtworkImportRowResult(
                    row_number=row_number,
                    slug="",
                    action="failed",
                    message="Missing title.",
                )
            )
            return

        slug = ((row.get("slug") or "").strip() or slugify(title))[:50]
        category_name = (row.get("category") or "").strip()
        if not category_name:
            result.failed += 1
            result.row_results.append(
                ArtworkImportRowResult(
                    row_number=row_number,
                    slug=slug,
                    action="failed",
                    message="Missing category.",
                )
            )
            return

        description = (row.get("description") or "").strip()
        image_filename = (row.get("image_filename") or "").strip()
        image_url = (row.get("image_url") or "").strip()
        is_featured = _parse_bool(row.get("is_featured"), False)
        is_published = _parse_bool(row.get("is_published"), True)

        category, category_created, category_note = self._resolve_category(category_name)
        if category is None:
            result.failed += 1
            result.row_results.append(
                ArtworkImportRowResult(
                    row_number=row_number,
                    slug=slug,
                    action="failed",
                    message=category_note or f'Category "{category_name}" does not exist.',
                )
            )
            return

        artwork = Artwork.objects.filter(slug=slug).first()
        if artwork and not self.update_existing:
            result.skipped += 1
            result.row_results.append(
                ArtworkImportRowResult(
                    row_number=row_number,
                    slug=slug,
                    action="skipped",
                    message="Artwork already exists and update_existing is disabled.",
                )
            )
            return

        image_bytes = None
        normalized_image_filename = _normalize_filename(image_filename) if image_filename else ""
        if normalized_image_filename:
            image_bytes = self._read_zip_member(
                archive,
                zip_index.get(normalized_image_filename),
            )
            if image_bytes is None:
                result.failed += 1
                result.row_results.append(
                    ArtworkImportRowResult(
                        row_number=row_number,
                        slug=slug,
                        action="failed",
                        message=f'Image "{image_filename}" was not found in the uploaded ZIP.',
                    )
                )
                return

        action = "updated" if artwork else "created"
        message = []
        if category_created:
            message.append(f'Created category "{category_name}"')
        elif category_note:
            message.append(category_note)
        if image_bytes:
            message.append(f'Attached image "{image_filename}"')
        elif image_url:
            message.append("Using image_url only")

        if self.dry_run:
            if artwork:
                result.updated += 1
            else:
                result.created += 1
            result.row_results.append(
                ArtworkImportRowResult(
                    row_number=row_number,
                    slug=slug,
                    action=f"dry-run-{action}",
                    message=", ".join(message) or "Validated successfully.",
                )
            )
            return

        with transaction.atomic():
            artwork = artwork or Artwork(slug=slug)
            artwork.title = title
            artwork.category = category
            artwork.description = description
            artwork.is_featured = is_featured
            artwork.is_published = is_published
            artwork.image_url = image_url
            artwork.save()

            if image_bytes:
                file_extension = Path(image_filename).suffix or ".png"
                artwork.image.save(
                    f"{slug}{file_extension}",
                    ContentFile(image_bytes),
                    save=True,
                )

        if action == "created":
            result.created += 1
        else:
            result.updated += 1
        result.row_results.append(
            ArtworkImportRowResult(
                row_number=row_number,
                slug=slug,
                action=action,
                message=", ".join(message) or "Imported successfully.",
            )
        )
