from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F, Min, Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsSuperUser
from apps.generator.models import MockupTemplatePart
from apps.printify.services import PrintifyError, sync_product_variants_from_printify

from .models import NotificationSubscription, Product, ProductCategory
from .pagination import StandardResultsSetPagination
from .serializers import (
    AdminNotificationSubscriptionSerializer,
    AdminProductCategorySerializer,
    AdminProductSerializer,
    NotificationSubscriptionSerializer,
    ProductCategorySerializer,
    ProductSerializer,
)
from .services import activate_product, deactivate_product, sellable_variant_exists_subquery

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
            .prefetch_related(
                "variants",
                # ProductSerializer._fallback_template_image_url() reads mockup_template.parts
                # (only when the product has no photo of its own) — prefetch it here or that
                # rare fallback N+1s per row once enough products hit it.
                Prefetch(
                    "mockup_template__parts",
                    queryset=MockupTemplatePart.objects.only("id", "template_id", "name", "base_image"),
                ),
            )
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
    queryset = _PUBLIC_PRODUCTS.select_related("category", "mockup_template").prefetch_related(
        "variants",
        Prefetch(
            "mockup_template__parts",
            queryset=MockupTemplatePart.objects.only("id", "template_id", "name", "base_image"),
        ),
    )
    serializer_class = ProductSerializer
    lookup_field = "slug"


class NotificationSubscriptionCreateView(CreateAPIView):
    serializer_class = NotificationSubscriptionSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(user=user)


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminProductCategoryListCreateView(ListCreateAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = AdminProductCategorySerializer
    permission_classes = [IsSuperUser]


class AdminProductCategoryDetailView(RetrieveUpdateDestroyAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = AdminProductCategorySerializer
    permission_classes = [IsSuperUser]


class AdminProductListCreateView(ListCreateAPIView):
    queryset = Product.objects.select_related("category", "mockup_template").all().order_by("-created_at")
    serializer_class = AdminProductSerializer
    permission_classes = [IsSuperUser]


class AdminProductDetailView(RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.select_related("category", "mockup_template").all()
    serializer_class = AdminProductSerializer
    permission_classes = [IsSuperUser]


class AdminProductActivateView(APIView):
    """Mirrors apps.shop.admin.ProductAdmin's "Activate selected products" action — runs the same
    validate_product_can_be_activated() gate, never lets the frontend flip is_active directly."""

    permission_classes = [IsSuperUser]

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        try:
            activate_product(product)
        except DjangoValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AdminProductSerializer(product).data)


class AdminProductDeactivateView(APIView):
    permission_classes = [IsSuperUser]

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        deactivate_product(product)
        return Response(AdminProductSerializer(product).data)


class AdminProductSyncVariantsView(APIView):
    """Mirrors apps.shop.admin.ProductAdmin's "Sync variants from mapped Printify print provider"
    action."""

    permission_classes = [IsSuperUser]

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        try:
            summary = sync_product_variants_from_printify(product)
        except PrintifyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(summary)


class AdminNotificationSubscriptionListView(ListAPIView):
    queryset = NotificationSubscription.objects.select_related("product", "user").all()
    serializer_class = AdminNotificationSubscriptionSerializer
    permission_classes = [IsSuperUser]


class AdminNotificationSubscriptionDeleteView(DestroyAPIView):
    queryset = NotificationSubscription.objects.all()
    permission_classes = [IsSuperUser]
