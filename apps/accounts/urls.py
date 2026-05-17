from django.urls import path

from .views import MaintenanceAccessView, MaintenanceStatusView, MeView, RegisterView


urlpatterns = [
    path("maintenance-status/", MaintenanceStatusView.as_view(), name="maintenance-status"),
    path("maintenance-access/", MaintenanceAccessView.as_view(), name="maintenance-access"),
    path("register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
]
