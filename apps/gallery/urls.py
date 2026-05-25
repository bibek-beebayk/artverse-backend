from django.urls import path

from .views import (
    ArtworkDetailView,
    ArtworkListView,
    CategoryListView,
    CollectionListView,
    FavoriteListView,
    FavoriteToggleView,
    VideoClipListView,
)


urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path("collections/", CollectionListView.as_view(), name="collection-list"),
    path("artworks/", ArtworkListView.as_view(), name="artwork-list"),
    path("artworks/<slug:slug>/", ArtworkDetailView.as_view(), name="artwork-detail"),
    path("videos/", VideoClipListView.as_view(), name="video-list"),
    path("favorites/", FavoriteListView.as_view(), name="favorite-list"),
    path("favorites/toggle/", FavoriteToggleView.as_view(), name="favorite-toggle"),
]
