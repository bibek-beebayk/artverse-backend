from django.urls import path

from .views import (
    DesignProjectDetailView,
    DesignProjectDuplicateView,
    DesignProjectListCreateView,
    GeneratedImageListView,
    GenerationRequestListCreateView,
    MockupRenderDetailView,
    MockupRenderListCreateView,
    MockupTemplateListView,
    ProductVariantListView,
)


urlpatterns = [
    path("requests/", GenerationRequestListCreateView.as_view(), name="generation-request-list-create"),
    path("images/", GeneratedImageListView.as_view(), name="generated-image-list"),
    path("mockup-templates/", MockupTemplateListView.as_view(), name="mockup-template-list"),
    path("product-variants/", ProductVariantListView.as_view(), name="product-variant-list"),
    path("mockup-renders/", MockupRenderListCreateView.as_view(), name="mockup-render-list-create"),
    path("mockup-renders/<int:pk>/", MockupRenderDetailView.as_view(), name="mockup-render-detail"),
    path("design-projects/", DesignProjectListCreateView.as_view(), name="design-project-list-create"),
    path("design-projects/<int:pk>/", DesignProjectDetailView.as_view(), name="design-project-detail"),
    path(
        "design-projects/<int:pk>/duplicate/",
        DesignProjectDuplicateView.as_view(),
        name="design-project-duplicate",
    ),
]
