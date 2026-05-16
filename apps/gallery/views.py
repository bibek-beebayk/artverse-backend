from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Artwork, Category, Favorite, VideoClip
from .serializers import (
    ArtworkSerializer,
    CategorySerializer,
    FavoriteSerializer,
    FavoriteToggleSerializer,
    VideoClipSerializer,
)


class CategoryListView(ListAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ArtworkListView(ListAPIView):
    serializer_class = ArtworkSerializer

    def get_queryset(self):
        queryset = Artwork.objects.filter(is_published=True).select_related("category")
        category_slug = self.request.query_params.get("category")
        featured = self.request.query_params.get("featured")

        if category_slug and category_slug.lower() != "all":
            queryset = queryset.filter(category__slug=category_slug)
        if featured in {"true", "1"}:
            queryset = queryset.filter(is_featured=True)
        return queryset


class ArtworkDetailView(RetrieveAPIView):
    queryset = Artwork.objects.filter(is_published=True).select_related("category")
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
            "artwork", "artwork__category"
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
