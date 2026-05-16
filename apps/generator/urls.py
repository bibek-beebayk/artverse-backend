from django.urls import path

from .views import GeneratedImageListView, GenerationRequestListCreateView


urlpatterns = [
    path("requests/", GenerationRequestListCreateView.as_view(), name="generation-request-list-create"),
    path("images/", GeneratedImageListView.as_view(), name="generated-image-list"),
]
