from django.db.models import F, Min, Q
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView

from .models import Product, ProductCategory
from .pagination import StandardResultsSetPagination
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

# Whitelist only — never pass a raw `?ordering=` value straight to .order_by(), that would let a
# client sort (or attempt to inject) on an arbitrary field/relation. `starting_price` orders by
# `_min_sellable_cost` (see below), an approximation of the serializer's true `starting_price`:
# it's the minimum `base_cost` among a product's sellable-shaped variants, computed as a single
# SQL aggregate so the list stays paginated efficiently. It deliberately does NOT include markup
# (that depends on PricingRule lookups done in Python by apps.cart.pricing / apps.shop.services
# for the exact per-product display value) — a reasonable, documented tradeoff for *ordering*,
# never used for what's actually displayed or charged.
_ORDERING_FIELDS = {
    "name": "name",
    "-name": "-name",
    "starting_price": "_min_sellable_cost",
    "-starting_price": "-_min_sellable_cost",
    "created_at": "created_at",
    "-created_at": "-created_at",
}


class ProductCategoryListView(ListAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer


class ProductListView(ListAPIView):
    """Public product catalogue — search, category/product-type filters, whitelisted ordering,
    and pagination (see .pagination.StandardResultsSetPagination). All filtering happens in SQL
    against `_PUBLIC_PRODUCTS` (already active + has-a-sellable-variant); nothing here loosens
    that base visibility rule."""

    serializer_class = ProductSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = (
            _PUBLIC_PRODUCTS.select_related("category", "mockup_template")
            .prefetch_related("variants")
            .annotate(
                _min_sellable_cost=Min(
                    "variants__base_cost",
                    filter=Q(
                        variants__is_available=True,
                        variants__base_cost__isnull=False,
                        variants__template_id=F("mockup_template_id"),
                    )
                    & (Q(variants__external_provider="") | ~Q(variants__external_variant_id="")),
                )
            )
        )

        category_slug = self.request.query_params.get("category")
        if category_slug and category_slug.lower() != "all":
            queryset = queryset.filter(category__slug=category_slug)

        product_type = self.request.query_params.get("product_type")
        if product_type and product_type.lower() != "all":
            queryset = queryset.filter(mockup_template__product_type=product_type)

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))

        # Always deterministically ordered — an unordered queryset paginates inconsistently
        # (possible duplicate/skipped rows across page boundaries). An unrecognized `ordering`
        # value is silently ignored (never passed raw to .order_by()) and falls back to this
        # same stable default, not to "whatever the database feels like."
        ordering = self.request.query_params.get("ordering")
        order_field = _ORDERING_FIELDS.get(ordering)
        queryset = queryset.order_by(order_field, "id") if order_field else queryset.order_by("-created_at", "id")

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
