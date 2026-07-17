from django.urls import path

from .views import (
    PrintifyBlueprintDetailView,
    PrintifyBlueprintListView,
    PrintifyBlueprintMapView,
    PrintifyConnectionStatusView,
    PrintifySyncBlueprintsView,
    PrintifySyncProvidersView,
)


urlpatterns = [
    path("status/", PrintifyConnectionStatusView.as_view(), name="printify-status"),
    path("blueprints/", PrintifyBlueprintListView.as_view(), name="printify-blueprint-list"),
    path("blueprints/<int:pk>/", PrintifyBlueprintDetailView.as_view(), name="printify-blueprint-detail"),
    path("blueprints/<int:pk>/map/", PrintifyBlueprintMapView.as_view(), name="printify-blueprint-map"),
    path(
        "blueprints/<int:pk>/sync-providers/",
        PrintifySyncProvidersView.as_view(),
        name="printify-blueprint-sync-providers",
    ),
    path("sync-blueprints/", PrintifySyncBlueprintsView.as_view(), name="printify-sync-blueprints"),
]
