from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.decorators.cache import cache_control
from django.views.static import serve as static_serve
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/gallery/", include("apps.gallery.urls")),
    path("api/shop/", include("apps.shop.urls")),
    path("api/generator/", include("apps.generator.urls")),
    path("api/cart/", include("apps.cart.urls")),
    path("api/printify/", include("apps.printify.urls")),
]

if settings.DEBUG:
    # Same as django.conf.urls.static.static(), but with Cache-Control added — the plain
    # django.views.static.serve view only sets Last-Modified, so the browser revalidates (a
    # network round trip, even if it gets a fast 304) on every single navigation instead of
    # serving straight from its local cache. Dev-only; production media goes through
    # RailwayBucketMediaStorage (config/storage_backends.py) or a real static-file server.
    urlpatterns += [
        path(
            f"{settings.MEDIA_URL.lstrip('/')}<path:path>",
            cache_control(public=True, max_age=86400)(static_serve),
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
