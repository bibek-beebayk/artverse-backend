from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
import json
import hashlib

from apps.gallery.models import Artwork

from .models import DesignPlacement, DesignProject, GeneratedImage, GeneratedPrintFile, GenerationRequest, MockupRender, MockupTemplate, ProductVariant
from .serializers import (
    DesignProjectListSerializer,
    DesignProjectSerializer,
    DesignProjectWriteSerializer,
    GeneratedImageSerializer,
    GenerationRequestSerializer,
    MockupRenderCreateSerializer,
    MockupRenderSerializer,
    MockupTemplateSerializer,
    ProductVariantSerializer,
    _placement_write_data_to_model_fields,
)
from .services import (
    build_mockup_cache_key,
    create_or_reuse_print_file,
    ensure_source_design_asset,
    is_placement_printable,
    process_mockup_render,
    resolve_source_fingerprint,
)


ALLOWED_DESIGN_PROJECT_ORDERING = {"updated_at", "-updated_at", "created_at", "-created_at", "name", "-name"}


def _placements_prefetch():
    return Prefetch(
        "placements",
        queryset=DesignPlacement.objects.select_related(
            "template_part", "source_artwork", "source_generated_image", "preview_render"
        ),
    )


def _sync_design_placements(design_project: DesignProject, placements_data: list, *, replace: bool) -> None:
    """Create/update DesignPlacement rows from validated write-serializer data.

    replace=True  (PUT):   the given list is authoritative — parts omitted from it are deleted.
    replace=False (PATCH): only touches the parts present in the list; other parts are untouched.
    """

    existing_by_part = {placement.part_name: placement for placement in design_project.placements.all()}
    seen_parts = set()

    for item in placements_data:
        part_name = item["part_name"]
        seen_parts.add(part_name)
        fields = _placement_write_data_to_model_fields(item)
        template_part = item.get("_template_part")

        existing = existing_by_part.get(part_name)
        if existing:
            for attr, value in fields.items():
                setattr(existing, attr, value)
            existing.template_part = template_part
            existing.save()
        else:
            DesignPlacement.objects.create(design_project=design_project, template_part=template_part, **fields)

    if replace:
        for part_name, placement in existing_by_part.items():
            if part_name not in seen_parts:
                placement.delete()


def _generate_duplicate_name(user, base_name: str) -> str:
    base_name = (base_name or "Untitled Design").strip() or "Untitled Design"
    candidate = f"{base_name} Copy"
    if not DesignProject.objects.filter(user=user, name=candidate).exists():
        return candidate

    counter = 2
    while DesignProject.objects.filter(user=user, name=f"{candidate} {counter}").exists():
        counter += 1
    return f"{candidate} {counter}"


class GenerationRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = GenerationRequest.objects.filter(user=request.user)
        serializer = GenerationRequestSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = GenerationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        generation_request = GenerationRequest.objects.create(
            user=request.user,
            prompt=serializer.validated_data["prompt"],
            style=serializer.validated_data.get("style", ""),
            provider=serializer.validated_data.get("provider", "gemini"),
            model_name=serializer.validated_data.get("model_name", ""),
        )
        response_serializer = GenerationRequestSerializer(generation_request)
        return Response(
            {
                "request": response_serializer.data,
                "message": "Generation request accepted. Provider integration should be added server-side next.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class GeneratedImageListView(ListAPIView):
    serializer_class = GeneratedImageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return GeneratedImage.objects.filter(user=self.request.user)


class MockupTemplateListView(ListAPIView):
    serializer_class = MockupTemplateSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = MockupTemplate.objects.filter(is_active=True)
        product_type = self.request.query_params.get("product_type")
        if product_type:
            queryset = queryset.filter(product_type=product_type)
        return queryset


class ProductVariantListView(ListAPIView):
    serializer_class = ProductVariantSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = ProductVariant.objects.filter(is_available=True).select_related("template", "product")
        template_id = self.request.query_params.get("template_id")
        if template_id:
            queryset = queryset.filter(template_id=template_id)
        product_id = self.request.query_params.get("product_id")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return queryset


class MockupRenderListCreateView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        queryset = MockupRender.objects.select_related("template", "generated_image")
        generated_image_id = request.query_params.get("generated_image_id")
        source_image_url = request.query_params.get("source_image_url")
        status_filter = request.query_params.get("status")

        if generated_image_id:
            queryset = queryset.filter(generated_image_id=generated_image_id)
        if source_image_url:
            queryset = queryset.filter(source_image_url=source_image_url)
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        serializer = MockupRenderSerializer(queryset[:50], many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = MockupRenderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        template = get_object_or_404(
            MockupTemplate.objects.filter(is_active=True),
            pk=serializer.validated_data["template_id"],
        )

        generated_image = None
        generated_image_id = serializer.validated_data.get("generated_image_id")
        if generated_image_id:
            generated_image = get_object_or_404(GeneratedImage, pk=generated_image_id)

        artwork = None
        artwork_id = serializer.validated_data.get("artwork_id")
        if artwork_id:
            artwork = get_object_or_404(Artwork, pk=artwork_id)

        source_image_url = serializer.validated_data.get("source_image_url", "").strip()
        persisted_source_image_url = "" if source_image_url.startswith("data:image/") else source_image_url
        source_prompt = serializer.validated_data.get("source_prompt", "").strip()
        part_name = serializer.validated_data.get("part_name", "").strip()
        if part_name and not template.parts.filter(name=part_name).exists():
            return Response(
                {"part_name": [f"Template '{template.slug}' has no part named '{part_name}'."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        variant_color = serializer.validated_data.get("variant_color", "").strip()
        variant_size = serializer.validated_data.get("variant_size", "").strip()
        placement_override = serializer.validated_data.get("placement_override", {}) or {}
        crop_override = serializer.validated_data.get("crop_override", {}) or {}
        text_elements = serializer.validated_data.get("text_elements", []) or []

        source_fingerprint = resolve_source_fingerprint(
            generated_image=generated_image,
            artwork=artwork,
            source_image_url=source_image_url,
        )
        source_asset = None
        if artwork is not None or source_image_url:
            source_asset = ensure_source_design_asset(
                source_fingerprint=source_fingerprint,
                source_image_url=source_image_url,
                artwork=artwork,
                title=source_prompt or getattr(artwork, "title", ""),
            )
        cache_key = build_mockup_cache_key(
            template=template,
            source_fingerprint=source_fingerprint,
            part_name=part_name,
            variant_color=variant_color,
            variant_size=variant_size,
            placement_override=placement_override,
            crop_override=crop_override,
        )
        cache_key = hashlib.sha256(
            (cache_key + json.dumps(text_elements, sort_keys=True)).encode("utf-8")
        ).hexdigest()

        mockup_render, created = MockupRender.objects.get_or_create(
            cache_key=cache_key,
            defaults={
                "user": request.user if request.user.is_authenticated else None,
                "generated_image": generated_image,
                "artwork": artwork,
                "source_asset": source_asset,
                "source_image_url": persisted_source_image_url,
                "source_prompt": source_prompt or getattr(generated_image, "prompt", "") or getattr(artwork, "title", ""),
                "source_fingerprint": source_fingerprint,
                "template": template,
                "part_name": part_name,
                "variant_color": variant_color,
                "variant_size": variant_size,
                "placement_override": placement_override,
                "crop_override": crop_override,
                "text_elements": text_elements,
                "processing_notes": {
                    "pipeline": "backend-mockup",
                    "next_step": "Run async compositing worker",
                },
            },
        )

        changed_fields = []
        if artwork is not None and mockup_render.artwork_id != artwork.pk:
            mockup_render.artwork = artwork
            changed_fields.append("artwork")
        if source_asset is not None and mockup_render.source_asset_id != source_asset.pk:
            mockup_render.source_asset = source_asset
            changed_fields.append("source_asset")
        if mockup_render.source_image_url != persisted_source_image_url:
            mockup_render.source_image_url = persisted_source_image_url
            changed_fields.append("source_image_url")
        if source_prompt and mockup_render.source_prompt != source_prompt:
            mockup_render.source_prompt = source_prompt
            changed_fields.append("source_prompt")
        if mockup_render.crop_override != crop_override:
            mockup_render.crop_override = crop_override
            changed_fields.append("crop_override")
        if mockup_render.text_elements != text_elements:
            mockup_render.text_elements = text_elements
            changed_fields.append("text_elements")
        if changed_fields:
            changed_fields.append("updated_at")
            mockup_render.save(update_fields=changed_fields)

        if (
            created
            or mockup_render.status in {MockupRender.Status.PENDING, MockupRender.Status.FAILED}
        ):
            mockup_render = process_mockup_render(mockup_render)

        response_serializer = MockupRenderSerializer(mockup_render)
        return Response(
            {
                "created": created,
                "render": response_serializer.data,
                "message": (
                    "Mockup render processed."
                    if created and mockup_render.status == MockupRender.Status.READY
                    else "Mockup render failed during processing."
                    if mockup_render.status == MockupRender.Status.FAILED
                    else "Existing cached mockup render returned."
                ),
            },
            status=(
                status.HTTP_201_CREATED
                if created and mockup_render.status == MockupRender.Status.READY
                else status.HTTP_422_UNPROCESSABLE_ENTITY
                if mockup_render.status == MockupRender.Status.FAILED
                else status.HTTP_200_OK
            ),
        )


class MockupRenderDetailView(RetrieveAPIView):
    queryset = MockupRender.objects.select_related("template", "generated_image")
    serializer_class = MockupRenderSerializer
    permission_classes = [AllowAny]


class DesignProjectListCreateView(APIView):
    """List the current user's saved design projects, or save a new one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = (
            DesignProject.objects.filter(user=request.user)
            .select_related("product", "mockup_template", "selected_variant")
            .annotate(placement_count_annotated=Count("placements", distinct=True))
            .prefetch_related(
                Prefetch(
                    "placements",
                    queryset=DesignPlacement.objects.only("id", "design_project_id", "part_name", "preview_url"),
                )
            )
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)

        product_id = request.query_params.get("product")
        if product_id:
            queryset = queryset.filter(product_id=product_id)

        template_id = request.query_params.get("template")
        if template_id:
            queryset = queryset.filter(mockup_template_id=template_id)

        ordering = request.query_params.get("ordering")
        if ordering in ALLOWED_DESIGN_PROJECT_ORDERING:
            queryset = queryset.order_by(ordering)

        serializer = DesignProjectListSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = DesignProjectWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            design_project = DesignProject.objects.create(
                user=request.user,
                name=data.get("name", ""),
                product=data["_product"],
                mockup_template=data["_template"],
                selected_variant=data["_variant"],
                selected_color=data.get("selected_color", ""),
                selected_size=data.get("selected_size", ""),
                source_artwork_id=data.get("source_artwork_id"),
                source_generated_image_id=data.get("source_generated_image_id"),
                source_image_url=data.get("source_image_url", ""),
                source_prompt=data.get("source_prompt", ""),
                thumbnail_url=data.get("thumbnail_url", ""),
                metadata={**data.get("metadata", {}), "schema_version": 1},
                status=DesignProject.Status.DRAFT,
            )
            _sync_design_placements(design_project, data.get("placements", []), replace=True)

        response_serializer = DesignProjectSerializer(design_project)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class DesignProjectDetailView(APIView):
    """Reopen, rename/update (PUT = full replace, PATCH = partial), or delete a single saved
    design project. Owner-only (404, not 403, for another user's project)."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        return get_object_or_404(
            DesignProject.objects.select_related(
                "user", "product", "mockup_template", "selected_variant"
            ).prefetch_related(_placements_prefetch()),
            pk=pk,
            user=request.user,
        )

    def get(self, request, pk):
        design_project = self.get_object(request, pk)
        serializer = DesignProjectSerializer(design_project)
        return Response(serializer.data)

    def _write(self, request, pk, *, partial: bool):
        design_project = self.get_object(request, pk)
        serializer = DesignProjectWriteSerializer(
            instance=design_project, data=request.data, partial=partial, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            if "name" in data:
                design_project.name = data["name"]
            design_project.product = data["_product"]
            design_project.mockup_template = data["_template"]
            design_project.selected_variant = data["_variant"]
            if "selected_color" in data:
                design_project.selected_color = data["selected_color"]
            if "selected_size" in data:
                design_project.selected_size = data["selected_size"]
            if "source_artwork_id" in data:
                design_project.source_artwork_id = data["source_artwork_id"]
            if "source_generated_image_id" in data:
                design_project.source_generated_image_id = data["source_generated_image_id"]
            if "source_image_url" in data:
                design_project.source_image_url = data["source_image_url"]
            if "source_prompt" in data:
                design_project.source_prompt = data["source_prompt"]
            if "thumbnail_url" in data:
                design_project.thumbnail_url = data["thumbnail_url"]
            if "metadata" in data:
                design_project.metadata = {**data["metadata"], "schema_version": 1}
            design_project.save()

            if "placements" in data:
                _sync_design_placements(design_project, data["placements"], replace=not partial)
                # get_object() prefetched `placements` before this method mutated some of those
                # same rows in place (.save()/.delete()); bust the cache so the response below
                # re-queries fresh instead of serializing the stale pre-mutation snapshot.
                design_project._prefetched_objects_cache.pop("placements", None)

        response_serializer = DesignProjectSerializer(design_project)
        return Response(response_serializer.data)

    def put(self, request, pk):
        """Full replacement: the supplied placements array is authoritative — placements for
        parts omitted from it are deleted."""
        return self._write(request, pk, partial=False)

    def patch(self, request, pk):
        """Partial update: supplied placements are upserted; placements for parts NOT present
        in the payload are left untouched (a PATCH touching only 'front' must not delete
        'back'/sleeve placements)."""
        return self._write(request, pk, partial=True)

    def delete(self, request, pk):
        design_project = self.get_object(request, pk)
        design_project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DesignProjectDuplicateView(APIView):
    """Clone a saved design project (and all its placements) into a new draft. Owner-only."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        source = get_object_or_404(
            DesignProject.objects.prefetch_related(_placements_prefetch()),
            pk=pk,
            user=request.user,
        )

        with transaction.atomic():
            duplicate = DesignProject.objects.create(
                user=request.user,
                name=_generate_duplicate_name(request.user, source.name),
                product=source.product,
                mockup_template=source.mockup_template,
                selected_variant=source.selected_variant,
                selected_color=source.selected_color,
                selected_size=source.selected_size,
                source_artwork=source.source_artwork,
                source_generated_image=source.source_generated_image,
                source_image_url=source.source_image_url,
                source_prompt=source.source_prompt,
                thumbnail_url=source.thumbnail_url,
                metadata=dict(source.metadata),
                status=DesignProject.Status.DRAFT,
            )
            for placement in source.placements.all():
                DesignPlacement.objects.create(
                    design_project=duplicate,
                    part_name=placement.part_name,
                    template_part=placement.template_part,
                    source_artwork=placement.source_artwork,
                    source_generated_image=placement.source_generated_image,
                    source_image_url=placement.source_image_url,
                    source_prompt=placement.source_prompt,
                    x=placement.x,
                    y=placement.y,
                    width=placement.width,
                    height=placement.height,
                    rotation=placement.rotation,
                    opacity=placement.opacity,
                    corner_radius=placement.corner_radius,
                    fit=placement.fit,
                    crop_left=placement.crop_left,
                    crop_top=placement.crop_top,
                    crop_width=placement.crop_width,
                    crop_height=placement.crop_height,
                    text_elements=placement.text_elements,
                    preview_render=placement.preview_render,
                    preview_url=placement.preview_url,
                    print_file_url=placement.print_file_url,
                    metadata=dict(placement.metadata),
                )

        serializer = DesignProjectSerializer(duplicate)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


_PRINT_FILE_STATUS_MAP = {
    GeneratedPrintFile.Status.READY: "completed",
    GeneratedPrintFile.Status.FAILED: "failed",
    GeneratedPrintFile.Status.PROCESSING: "processing",
    GeneratedPrintFile.Status.PENDING: "queued",
}


def _resolve_output_file_url(record: GeneratedPrintFile) -> str | None:
    if not record.output_file:
        return None
    try:
        return record.output_file.url
    except Exception:
        return None


def _print_file_part_payload(placement: DesignPlacement, record: GeneratedPrintFile, *, reused: bool) -> dict:
    api_status = _PRINT_FILE_STATUS_MAP.get(record.status, "failed")
    return {
        "part_name": placement.part_name,
        "status": api_status,
        "print_file_url": _resolve_output_file_url(record) if api_status == "completed" else None,
        "width": record.width or None,
        "height": record.height or None,
        "dpi": record.dpi or None,
        "reused": reused,
        "error": record.error_message or None,
    }


class DesignProjectGeneratePrintFilesView(APIView):
    """Generate (or reuse — see create_or_reuse_print_file's signature-based cache) production
    print files for every printable, configured part of a saved design project. Owner-only,
    same 404-not-403 pattern as the rest of this app. Synchronous — same as mockup-render
    generation, there's no task queue yet (roadmap Section 8). Empty parts (nothing configured
    at all) are silently omitted from the response rather than reported with a status; a part
    that *is* configured but can't be generated (missing template_part link, invalid production
    dimensions, etc.) is reported with status "failed" and a clear `error`."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        design_project = get_object_or_404(
            DesignProject.objects.prefetch_related(_placements_prefetch()), pk=pk, user=request.user
        )

        parts_response = []
        for placement in design_project.placements.all():
            if not is_placement_printable(placement) and not placement.text_elements:
                continue  # nothing configured on this part — skip silently, not an error

            template_part = placement.template_part
            if template_part is None:
                parts_response.append(
                    {
                        "part_name": placement.part_name,
                        "status": "failed",
                        "print_file_url": None,
                        "width": None,
                        "height": None,
                        "dpi": None,
                        "reused": False,
                        "error": "No template part is configured for this placement — a print file can't be generated without one.",
                    }
                )
                continue

            record, reused = create_or_reuse_print_file(placement=placement, template_part=template_part)

            if record.status == GeneratedPrintFile.Status.READY:
                placement.print_file_url = _resolve_output_file_url(record) or ""
                placement.save(update_fields=["print_file_url"])

            parts_response.append(_print_file_part_payload(placement, record, reused=reused))

        overall_status = "failed" if any(p["status"] == "failed" for p in parts_response) else "completed"
        return Response({"project_id": design_project.id, "status": overall_status, "parts": parts_response})


class DesignProjectPrintFilesView(APIView):
    """Read-only status of each configured part's most recent print-file generation attempt,
    without triggering a new one. Owner-only."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        design_project = get_object_or_404(
            DesignProject.objects.prefetch_related(_placements_prefetch()), pk=pk, user=request.user
        )

        parts_response = []
        for placement in design_project.placements.all():
            latest = placement.generated_print_files.order_by("-created_at").first()
            if latest is None:
                continue
            parts_response.append(_print_file_part_payload(placement, latest, reused=False))

        overall_status = "failed" if any(p["status"] == "failed" for p in parts_response) else "completed"
        return Response({"project_id": design_project.id, "status": overall_status, "parts": parts_response})
