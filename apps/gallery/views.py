from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsSuperUser
from apps.shop.pagination import StandardResultsSetPagination

from .models import Artwork, Category, Collection, Favorite, VideoClip
from .serializers import (
    AdminArtworkSerializer,
    AdminCategorySerializer,
    AdminCollectionSerializer,
    AdminFavoriteSerializer,
    AdminVideoClipSerializer,
    ArtworkSerializer,
    CategorySerializer,
    CollectionSerializer,
    FavoriteSerializer,
    FavoriteToggleSerializer,
    VideoClipSerializer,
)
from .services import ArtworkBulkImporter

# Whitelist only — see the matching note in apps.shop.views. `title`/`created_at` are real DB
# columns, no aggregate needed (unlike shop's `starting_price`).
_ORDERING_FIELDS = {
    "title": "title",
    "-title": "-title",
    "created_at": "created_at",
    "-created_at": "-created_at",
}


class CategoryListView(ListAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class CollectionListView(ListAPIView):
    queryset = Collection.objects.all()
    serializer_class = CollectionSerializer


class ArtworkListView(ListAPIView):
    """Public gallery listing — only `is_published=True` designs are ever visible here (admin-
    approved/uploaded artworks; there is no concept of a user-private gallery entry — private
    user-uploaded/AI-generated artwork lives on `generator.SourceDesignAsset`, a separate model,
    never in this queryset). Paginated (see .pagination.StandardResultsSetPagination) — the
    customization screen's "Choose from Gallery" selector fetches pages of this rather than the
    whole catalogue at once."""

    serializer_class = ArtworkSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = Artwork.objects.filter(is_published=True).select_related("category", "collection")
        category_slug = self.request.query_params.get("category")
        collection_slug = self.request.query_params.get("collection")
        featured = self.request.query_params.get("featured")
        search = self.request.query_params.get("search")

        if category_slug and category_slug.lower() != "all":
            queryset = queryset.filter(category__slug=category_slug)
        if collection_slug and collection_slug.lower() != "all":
            queryset = queryset.filter(collection__slug=collection_slug)
        if featured in {"true", "1"}:
            queryset = queryset.filter(is_featured=True)
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search))

        ordering = self.request.query_params.get("ordering")
        order_field = _ORDERING_FIELDS.get(ordering)
        if order_field:
            queryset = queryset.order_by(order_field, "id")

        return queryset


class ArtworkDetailView(RetrieveAPIView):
    queryset = Artwork.objects.filter(is_published=True).select_related("category", "collection")
    serializer_class = ArtworkSerializer
    lookup_field = "slug"


class VideoClipListView(ListAPIView):
    queryset = VideoClip.objects.filter(is_published=True)
    serializer_class = VideoClipSerializer


class FavoriteListView(ListAPIView):
    serializer_class = FavoriteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user).select_related(
            "artwork", "artwork__category", "artwork__collection"
        )


class FavoriteToggleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = FavoriteToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        artwork = get_object_or_404(Artwork, pk=serializer.validated_data["artwork_id"])
        favorite, created = Favorite.objects.get_or_create(user=request.user, artwork=artwork)

        if not created:
            favorite.delete()
            return Response({"favorited": False}, status=status.HTTP_200_OK)

        return Response({"favorited": True}, status=status.HTTP_201_CREATED)


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminCategoryListCreateView(ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer
    permission_classes = [IsSuperUser]


class AdminCategoryDetailView(RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer
    permission_classes = [IsSuperUser]


class AdminCollectionListCreateView(ListCreateAPIView):
    queryset = Collection.objects.all()
    serializer_class = AdminCollectionSerializer
    permission_classes = [IsSuperUser]


class AdminCollectionDetailView(RetrieveUpdateDestroyAPIView):
    queryset = Collection.objects.all()
    serializer_class = AdminCollectionSerializer
    permission_classes = [IsSuperUser]


class AdminArtworkListCreateView(ListCreateAPIView):
    queryset = Artwork.objects.select_related("category", "collection").all()
    serializer_class = AdminArtworkSerializer
    permission_classes = [IsSuperUser]
    pagination_class = StandardResultsSetPagination


class AdminArtworkDetailView(RetrieveUpdateDestroyAPIView):
    queryset = Artwork.objects.select_related("category", "collection").all()
    serializer_class = AdminArtworkSerializer
    permission_classes = [IsSuperUser]


class AdminArtworkBulkImportView(APIView):
    """Wraps apps.gallery.services.ArtworkBulkImporter — the same importer the Django admin's
    "Bulk import artworks" custom admin view already uses (apps.gallery.admin)."""

    permission_classes = [IsSuperUser]
    parser_classes = [MultiPartParser]

    def post(self, request):
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            return Response({"detail": "csv_file is required."}, status=status.HTTP_400_BAD_REQUEST)

        importer = ArtworkBulkImporter(
            update_existing=_parse_request_bool(request.data.get("update_existing"), True),
            auto_create_categories=_parse_request_bool(request.data.get("auto_create_categories"), True),
            dry_run=_parse_request_bool(request.data.get("dry_run"), False),
        )
        try:
            result = importer.import_from_files(
                csv_file=csv_file,
                images_zip_file=request.FILES.get("images_zip_file"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "created": result.created,
                "updated": result.updated,
                "skipped": result.skipped,
                "failed": result.failed,
                "row_results": [
                    {
                        "row_number": row.row_number,
                        "slug": row.slug,
                        "action": row.action,
                        "message": row.message,
                    }
                    for row in result.row_results
                ],
            }
        )


def _parse_request_bool(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class AdminVideoClipListCreateView(ListCreateAPIView):
    queryset = VideoClip.objects.all()
    serializer_class = AdminVideoClipSerializer
    permission_classes = [IsSuperUser]


class AdminVideoClipDetailView(RetrieveUpdateDestroyAPIView):
    queryset = VideoClip.objects.all()
    serializer_class = AdminVideoClipSerializer
    permission_classes = [IsSuperUser]


class AdminFavoriteListView(ListAPIView):
    queryset = Favorite.objects.select_related("user", "artwork").all()
    serializer_class = AdminFavoriteSerializer
    permission_classes = [IsSuperUser]
    pagination_class = StandardResultsSetPagination
