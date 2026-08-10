from django.urls import path

from .views import (
    AdminArtworkBulkImportView,
    AdminArtworkDetailView,
    AdminArtworkListCreateView,
    AdminCategoryDetailView,
    AdminCategoryListCreateView,
    AdminCollectionDetailView,
    AdminCollectionListCreateView,
    AdminFavoriteListView,
    AdminVideoClipDetailView,
    AdminVideoClipListCreateView,
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
    path("admin/categories/", AdminCategoryListCreateView.as_view(), name="admin-gallery-category-list"),
    path("admin/categories/<int:pk>/", AdminCategoryDetailView.as_view(), name="admin-gallery-category-detail"),
    path("admin/collections/", AdminCollectionListCreateView.as_view(), name="admin-collection-list"),
    path("admin/collections/<int:pk>/", AdminCollectionDetailView.as_view(), name="admin-collection-detail"),
    path("admin/artworks/", AdminArtworkListCreateView.as_view(), name="admin-artwork-list"),
    path("admin/artworks/<int:pk>/", AdminArtworkDetailView.as_view(), name="admin-artwork-detail"),
    path("admin/artworks/bulk-import/", AdminArtworkBulkImportView.as_view(), name="admin-artwork-bulk-import"),
    path("admin/videos/", AdminVideoClipListCreateView.as_view(), name="admin-video-list"),
    path("admin/videos/<int:pk>/", AdminVideoClipDetailView.as_view(), name="admin-video-detail"),
    path("admin/favorites/", AdminFavoriteListView.as_view(), name="admin-favorite-list"),
]
