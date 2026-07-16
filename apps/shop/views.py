from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView

from .models import Product, ProductCategory
from .serializers import (
    NotificationSubscriptionSerializer,
    ProductCategorySerializer,
    ProductSerializer,
)


class ProductCategoryListView(ListAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductListView(ListAPIView):
    serializer_class = ProductSerializer

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True).select_related("category", "mockup_template").prefetch_related(
            "variants"
        )
        category_slug = self.request.query_params.get("category")
        if category_slug and category_slug.lower() != "all":
            queryset = queryset.filter(category__slug=category_slug)
        return queryset


class ProductDetailView(RetrieveAPIView):
    queryset = Product.objects.filter(is_active=True).select_related("category", "mockup_template").prefetch_related(
        "variants"
    )
    serializer_class = ProductSerializer
    lookup_field = "slug"


class NotificationSubscriptionCreateView(CreateAPIView):
    serializer_class = NotificationSubscriptionSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(user=user)
