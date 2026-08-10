from django.urls import path

from .views import (
    AdminDashboardView,
    AdminSiteConfigurationView,
    AdminUserDetailView,
    AdminUserListView,
    GoogleLoginView,
    MaintenanceAccessView,
    MaintenanceStatusView,
    MeView,
    RegisterView,
)


urlpatterns = [
    path("google-login/", GoogleLoginView.as_view(), name="google-login"),
    path("maintenance-status/", MaintenanceStatusView.as_view(), name="maintenance-status"),
    path("maintenance-access/", MaintenanceAccessView.as_view(), name="maintenance-access"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
    path("admin/dashboard/", AdminDashboardView.as_view(), name="admin-dashboard"),
    path("admin/users/", AdminUserListView.as_view(), name="admin-user-list"),
    path("admin/users/<int:pk>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("admin/site-configuration/", AdminSiteConfigurationView.as_view(), name="admin-site-configuration"),
]
