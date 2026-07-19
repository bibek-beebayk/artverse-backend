from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView

from .models import Product, ProductCategory
from .serializers import (
    NotificationSubscriptionSerializer,
    ProductCategorySerializer,
    ProductSerializer,
)
from .services import sellable_variant_exists_subquery

# Shared by both public views: active AND has at least one sellable variant (see
# apps.shop.services.sellable_variant_exists_subquery — kept in sync with
# product_has_sellable_variant()'s Python-side criteria). One correlated EXISTS subquery
# evaluated per row in SQL, not a per-product Python check — avoids an N+1 query explosion on
# the list endpoint. Internal/admin code (Django admin, the activation service) intentionally
# does NOT go through this — it still needs to see inactive/incomplete products.
_PUBLIC_PRODUCTS = Product.objects.annotate(_has_sellable_variant=sellable_variant_exists_subquery()).filter(
    is_active=True, _has_sellable_variant=True
)


class ProductCategoryListView(ListAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductListView(ListAPIView):
    serializer_class = ProductSerializer

    def get_queryset(self):
        queryset = _PUBLIC_PRODUCTS.select_related("category", "mockup_template").prefetch_related("variants")
        category_slug = self.request.query_params.get("category")
        if category_slug and category_slug.lower() != "all":
            queryset = queryset.filter(category__slug=category_slug)
        return queryset


class ProductDetailView(RetrieveAPIView):
    queryset = _PUBLIC_PRODUCTS.select_related("category", "mockup_template").prefetch_related("variants")
    serializer_class = ProductSerializer
    lookup_field = "slug"


class NotificationSubscriptionCreateView(CreateAPIView):
    serializer_class = NotificationSubscriptionSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(user=user)
