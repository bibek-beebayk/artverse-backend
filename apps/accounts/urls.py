from django.urls import path

from .views import GoogleLoginView, MaintenanceAccessView, MaintenanceStatusView, MeView, RegisterView


urlpatterns = [
    path("google-login/", GoogleLoginView.as_view(), name="google-login"),
    path("maintenance-status/", MaintenanceStatusView.as_view(), name="maintenance-status"),
    path("maintenance-access/", MaintenanceAccessView.as_view(), name="maintenance-access"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
]
