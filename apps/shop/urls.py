from django.urls import path

from .views import (
    AdminNotificationSubscriptionDeleteView,
    AdminNotificationSubscriptionListView,
    AdminProductActivateView,
    AdminProductCategoryDetailView,
    AdminProductCategoryListCreateView,
    AdminProductDeactivateView,
    AdminProductDetailView,
    AdminProductListCreateView,
    AdminProductSyncVariantsView,
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
    path(
        "admin/categories/",
        AdminProductCategoryListCreateView.as_view(),
        name="admin-product-category-list",
    ),
    path(
        "admin/categories/<int:pk>/",
        AdminProductCategoryDetailView.as_view(),
        name="admin-product-category-detail",
    ),
    path("admin/products/", AdminProductListCreateView.as_view(), name="admin-product-list"),
    path("admin/products/<int:pk>/", AdminProductDetailView.as_view(), name="admin-product-detail"),
    path("admin/products/<int:pk>/activate/", AdminProductActivateView.as_view(), name="admin-product-activate"),
    path(
        "admin/products/<int:pk>/deactivate/",
        AdminProductDeactivateView.as_view(),
        name="admin-product-deactivate",
    ),
    path(
        "admin/products/<int:pk>/sync-variants/",
        AdminProductSyncVariantsView.as_view(),
        name="admin-product-sync-variants",
    ),
    path(
        "admin/notification-subscriptions/",
        AdminNotificationSubscriptionListView.as_view(),
        name="admin-notification-subscription-list",
    ),
    path(
        "admin/notification-subscriptions/<int:pk>/",
        AdminNotificationSubscriptionDeleteView.as_view(),
        name="admin-notification-subscription-detail",
    ),
]
