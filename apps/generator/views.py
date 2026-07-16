from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
import json
import hashlib

from apps.gallery.models import Artwork

from .models import DesignPlacement, DesignProject, GeneratedImage, GenerationRequest, MockupRender, MockupTemplate, ProductVariant
from .serializers import (
    DesignProjectSerializer,
    DesignProjectWriteSerializer,
    GeneratedImageSerializer,
    GenerationRequestSerializer,
    MockupRenderCreateSerializer,
    MockupRenderSerializer,
    MockupTemplateSerializer,
    ProductVariantSerializer,
)
from .services import (
    build_mockup_cache_key,
    ensure_source_design_asset,
    process_mockup_render,
    resolve_source_fingerprint,
)


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
        queryset = ProductVariant.objects.filter(is_available=True).select_related("template")
        template_id = self.request.query_params.get("template_id")
        if template_id:
            queryset = queryset.filter(template_id=template_id)
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
            .select_related("template", "selected_variant")
            .prefetch_related("placements")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        serializer = DesignProjectSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = DesignProjectWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        design_project = serializer.save(user=request.user, status=DesignProject.Status.SAVED)
        response_serializer = DesignProjectSerializer(design_project)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class DesignProjectDetailView(APIView):
    """Reopen, rename/update, or delete a single saved design project. Owner-only."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        return get_object_or_404(
            DesignProject.objects.select_related("template", "selected_variant").prefetch_related("placements"),
            pk=pk,
            user=request.user,
        )

    def get(self, request, pk):
        design_project = self.get_object(request, pk)
        serializer = DesignProjectSerializer(design_project)
        return Response(serializer.data)

    def patch(self, request, pk):
        design_project = self.get_object(request, pk)
        serializer = DesignProjectWriteSerializer(design_project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        response_serializer = DesignProjectSerializer(design_project)
        return Response(response_serializer.data)

    def delete(self, request, pk):
        design_project = self.get_object(request, pk)
        design_project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DesignProjectDuplicateView(APIView):
    """Clone a saved design project (and all its placements) into a new draft. Owner-only."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        source = get_object_or_404(
            DesignProject.objects.prefetch_related("placements"),
            pk=pk,
            user=request.user,
        )
        duplicate = DesignProject.objects.create(
            user=request.user,
            name=f"{source.name} (Copy)" if source.name else "",
            template=source.template,
            selected_colour=source.selected_colour,
            selected_variant=source.selected_variant,
            status=DesignProject.Status.DRAFT,
        )
        for placement in source.placements.all():
            DesignPlacement.objects.create(
                design_project=duplicate,
                product_part=placement.product_part,
                artwork=placement.artwork,
                generated_image=placement.generated_image,
                x_position=placement.x_position,
                y_position=placement.y_position,
                width=placement.width,
                height=placement.height,
                rotation=placement.rotation,
                opacity=placement.opacity,
                crop_data=placement.crop_data,
                corner_radius=placement.corner_radius,
                text_settings=placement.text_settings,
                preview_url=placement.preview_url,
                print_file_url=placement.print_file_url,
            )
        serializer = DesignProjectSerializer(duplicate)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
