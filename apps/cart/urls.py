from django.urls import path

from .views import (
    AdminCartListView,
    AdminCouponDetailView,
    AdminCouponListCreateView,
    AdminPricingConfigView,
    AdminPricingRuleDetailView,
    AdminPricingRuleListCreateView,
    AdminPrintAreaChargeDetailView,
    AdminPrintAreaChargeListCreateView,
    CartCouponView,
    CartDetailView,
    CartItemCreateView,
    CartItemDetailView,
    CartMergeView,
)

urlpatterns = [
    path("", CartDetailView.as_view(), name="cart-detail"),
    path("items/", CartItemCreateView.as_view(), name="cart-item-create"),
    path("items/<int:pk>/", CartItemDetailView.as_view(), name="cart-item-detail"),
    path("coupon/", CartCouponView.as_view(), name="cart-coupon"),
    path("merge/", CartMergeView.as_view(), name="cart-merge"),
    path("admin/pricing-configuration/", AdminPricingConfigView.as_view(), name="admin-pricing-configuration"),
    path(
        "admin/print-area-charges/",
        AdminPrintAreaChargeListCreateView.as_view(),
        name="admin-print-area-charge-list",
    ),
    path(
        "admin/print-area-charges/<int:pk>/",
        AdminPrintAreaChargeDetailView.as_view(),
        name="admin-print-area-charge-detail",
    ),
    path("admin/pricing-rules/", AdminPricingRuleListCreateView.as_view(), name="admin-pricing-rule-list"),
    path("admin/pricing-rules/<int:pk>/", AdminPricingRuleDetailView.as_view(), name="admin-pricing-rule-detail"),
    path("admin/coupons/", AdminCouponListCreateView.as_view(), name="admin-coupon-list"),
    path("admin/coupons/<int:pk>/", AdminCouponDetailView.as_view(), name="admin-coupon-detail"),
    path("admin/carts/", AdminCartListView.as_view(), name="admin-cart-list"),
]
