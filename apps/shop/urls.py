from django.urls import path

from .views import (
    NotificationSubscriptionCreateView,
    ProductCategoryListView,
    ProductDetailView,
    ProductListView,
)


urlpatterns = [
    path("categories/", ProductCategoryListView.as_view(), name="product-category-list"),
    path("products/", ProductListView.as_view(), name="product-list"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
    path("notifications/", NotificationSubscriptionCreateView.as_view(), name="product-notification-create"),
]
