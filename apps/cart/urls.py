from django.urls import path

from .views import CartCouponView, CartDetailView, CartItemCreateView, CartItemDetailView, CartMergeView

urlpatterns = [
    path("", CartDetailView.as_view(), name="cart-detail"),
    path("items/", CartItemCreateView.as_view(), name="cart-item-create"),
    path("items/<int:pk>/", CartItemDetailView.as_view(), name="cart-item-detail"),
    path("coupon/", CartCouponView.as_view(), name="cart-coupon"),
    path("merge/", CartMergeView.as_view(), name="cart-merge"),
]
