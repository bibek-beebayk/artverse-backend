from django.urls import path

from .views import (
    GeneratedImageListView,
    GenerationRequestListCreateView,
    MockupRenderDetailView,
    MockupRenderListCreateView,
    MockupTemplateListView,
)


urlpatterns = [
    path("requests/", GenerationRequestListCreateView.as_view(), name="generation-request-list-create"),
    path("images/", GeneratedImageListView.as_view(), name="generated-image-list"),
    path("mockup-templates/", MockupTemplateListView.as_view(), name="mockup-template-list"),
    path("mockup-renders/", MockupRenderListCreateView.as_view(), name="mockup-render-list-create"),
    path("mockup-renders/<int:pk>/", MockupRenderDetailView.as_view(), name="mockup-render-detail"),
]
