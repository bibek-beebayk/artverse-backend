from rest_framework import status
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsSuperUser
from apps.shop.pagination import StandardResultsSetPagination

from . import services
from .models import Cart, Coupon, PricingConfig, PricingRule, PrintAreaCharge
from .pricing import CouponError
from .serializers import (
    AdminCartSerializer,
    AdminCouponSerializer,
    AdminPricingConfigSerializer,
    AdminPricingRuleSerializer,
    AdminPrintAreaChargeSerializer,
    serialize_cart,
)


class CartDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cart = services.get_or_create_cart(request.user)
        return Response(serialize_cart(cart))


class CartItemCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        design_project_id = request.data.get("design_project_id")
        if not design_project_id:
            return Response({"design_project_id": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = int(request.data.get("quantity", 1))
        except (TypeError, ValueError):
            return Response({"quantity": ["Must be an integer."]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = services.add_design_project_to_cart(
                user=request.user, design_project_id=design_project_id, quantity=quantity
            )
        except ValueError as exc:
            return Response({"design_project_id": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        cart = result["item"].cart
        return Response(
            serialize_cart(cart), status=status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
        )


class CartItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        quantity = request.data.get("quantity")
        if quantity is None:
            return Response({"quantity": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return Response({"quantity": ["Must be an integer."]}, status=status.HTTP_400_BAD_REQUEST)

        item = services.update_cart_item_quantity(user=request.user, item_id=pk, quantity=quantity)
        return Response(serialize_cart(item.cart))

    def delete(self, request, pk):
        cart = services.get_or_create_cart(request.user)
        services.remove_cart_item(user=request.user, item_id=pk)
        return Response(serialize_cart(cart))


class CartCouponView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        code = request.data.get("code", "")
        try:
            cart = services.apply_coupon(user=request.user, code=code)
        except CouponError as exc:
            return Response({"code": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_cart(cart))

    def delete(self, request):
        cart = services.remove_coupon(user=request.user)
        return Response(serialize_cart(cart))


class CartMergeView(APIView):
    """Called once, right after login, with the guest (localStorage) cart's resolvable items —
    design_project_id + quantity only. See CartContext.tsx for how a guest item (which never has
    a real backend DesignProject) gets one created for it before this is called."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        entries = request.data.get("items", [])
        if not isinstance(entries, list):
            return Response({"items": ["Must be a list."]}, status=status.HTTP_400_BAD_REQUEST)
        result = services.merge_guest_cart(user=request.user, entries=entries)
        cart = services.get_or_create_cart(request.user)
        return Response({**result, "cart": serialize_cart(cart)})


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminPricingConfigView(RetrieveUpdateAPIView):
    serializer_class = AdminPricingConfigSerializer
    permission_classes = [IsSuperUser]

    def get_object(self):
        return PricingConfig.get_solo()


class AdminPrintAreaChargeListCreateView(ListCreateAPIView):
    queryset = PrintAreaCharge.objects.all()
    serializer_class = AdminPrintAreaChargeSerializer
    permission_classes = [IsSuperUser]


class AdminPrintAreaChargeDetailView(RetrieveUpdateDestroyAPIView):
    queryset = PrintAreaCharge.objects.all()
    serializer_class = AdminPrintAreaChargeSerializer
    permission_classes = [IsSuperUser]


class AdminPricingRuleListCreateView(ListCreateAPIView):
    queryset = PricingRule.objects.select_related("category", "product").all()
    serializer_class = AdminPricingRuleSerializer
    permission_classes = [IsSuperUser]


class AdminPricingRuleDetailView(RetrieveUpdateDestroyAPIView):
    queryset = PricingRule.objects.select_related("category", "product").all()
    serializer_class = AdminPricingRuleSerializer
    permission_classes = [IsSuperUser]


class AdminCouponListCreateView(ListCreateAPIView):
    queryset = Coupon.objects.all().order_by("-created_at")
    serializer_class = AdminCouponSerializer
    permission_classes = [IsSuperUser]


class AdminCouponDetailView(RetrieveUpdateDestroyAPIView):
    queryset = Coupon.objects.all()
    serializer_class = AdminCouponSerializer
    permission_classes = [IsSuperUser]


class AdminCartListView(ListAPIView):
    queryset = Cart.objects.select_related("user", "coupon").all().order_by("-updated_at")
    serializer_class = AdminCartSerializer
    permission_classes = [IsSuperUser]
    pagination_class = StandardResultsSetPagination
