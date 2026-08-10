import secrets
from uuid import uuid4
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import F, Q
from rest_framework import status
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .firebase_auth import FirebaseConfigurationError, verify_firebase_id_token
from .models import SiteConfiguration, User
from .permissions import IsSuperUser
from .serializers import (
    AdminUserSerializer,
    GoogleAuthSerializer,
    RegisterSerializer,
    SiteConfigurationSerializer,
    UserSerializer,
)


def validate_maintenance_token(token: str, access_key: str, token_max_age: int) -> bool:
    if not token:
        return False

    try:
        payload = signing.loads(token, max_age=token_max_age)
    except (signing.BadSignature, signing.SignatureExpired):
        return False

    return (
        payload.get("scope") == "maintenance-access"
        and payload.get("access_key") == access_key
    )


class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class MeView(RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            decoded_token = verify_firebase_id_token(serializer.validated_data["id_token"])
        except FirebaseConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception:
            return Response({"detail": "Invalid Firebase ID token."}, status=status.HTTP_400_BAD_REQUEST)

        firebase_uid = decoded_token.get("uid")
        email = decoded_token.get("email")
        email_verified = bool(decoded_token.get("email_verified"))

        if not firebase_uid or not email:
            return Response(
                {"detail": "Firebase token is missing required identity fields."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email_verified:
            return Response(
                {"detail": "Google email address must be verified before signing in."},
                status=status.HTTP_403_FORBIDDEN,
            )

        display_name = decoded_token.get("name", "")
        avatar = decoded_token.get("picture", "")

        with transaction.atomic():
            user = User.objects.filter(firebase_uid=firebase_uid).first()

            if not user:
                user = User.objects.filter(email__iexact=email).first()

            if not user:
                base_username = email.split("@", 1)[0][:120] or "artverse-user"
                username = base_username
                while User.objects.filter(username=username).exists():
                    username = f"{base_username[:110]}-{uuid4().hex[:8]}"

                user = User.objects.create(
                    username=username,
                    email=email,
                )

            updates = []
            if user.email.lower() != email.lower():
                user.email = email
                updates.append("email")
            if display_name and user.display_name != display_name:
                user.display_name = display_name
                updates.append("display_name")
            if avatar and user.avatar != avatar:
                user.avatar = avatar
                updates.append("avatar")
            if user.firebase_uid != firebase_uid:
                user.firebase_uid = firebase_uid
                updates.append("firebase_uid")
            if user.auth_provider != "google":
                user.auth_provider = "google"
                updates.append("auth_provider")

            if updates:
                user.save(update_fields=updates)

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            }
        )


class MaintenanceStatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        config = SiteConfiguration.get_solo()
        token = request.headers.get("X-Maintenance-Token", "")
        return Response(
            {
                "maintenance_mode": config.maintenance_mode,
                "maintenance_message": config.maintenance_message,
                "access_granted": (
                    False
                    if not config.maintenance_mode
                    else validate_maintenance_token(
                        token,
                        config.maintenance_access_key,
                        settings.MAINTENANCE_TOKEN_MAX_AGE,
                    )
                ),
            }
        )


class MaintenanceAccessView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        config = SiteConfiguration.get_solo()

        if not config.maintenance_mode:
            return Response(
                {
                    "maintenance_mode": False,
                    "maintenance_message": config.maintenance_message,
                    "access_granted": True,
                }
            )

        if not config.maintenance_access_key:
            return Response(
                {"detail": "Maintenance access is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        access_key = str(request.data.get("access_key", "")).strip()
        if not secrets.compare_digest(access_key, config.maintenance_access_key):
            return Response(
                {"detail": "Invalid maintenance access key."},
                status=status.HTTP_403_FORBIDDEN,
            )

        token = signing.dumps(
            {
                "scope": "maintenance-access",
                "access_key": config.maintenance_access_key,
            }
        )
        return Response(
            {
                "maintenance_mode": True,
                "maintenance_message": config.maintenance_message,
                "access_granted": True,
                "token": token,
            }
        )


# --- Admin management panel (superuser-only) ---------------------------------------------


class AdminUserListView(ListAPIView):
    """Users & Access — flags only (is_staff/is_superuser/is_artist/is_active), never
    identity/password fields. Search by username/email/display_name via ?search=."""

    serializer_class = AdminUserSerializer
    permission_classes = [IsSuperUser]

    def get_queryset(self):
        queryset = User.objects.all().order_by("-date_joined")
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search) | Q(email__icontains=search) | Q(display_name__icontains=search)
            )
        return queryset


class AdminUserDetailView(RetrieveUpdateAPIView):
    queryset = User.objects.all()
    serializer_class = AdminUserSerializer
    permission_classes = [IsSuperUser]


class AdminSiteConfigurationView(RetrieveUpdateAPIView):
    """Singleton — Site → Site Configuration. Same get_solo() pattern the Django admin's own
    SiteConfigurationAdmin uses (apps/accounts/admin.py)."""

    serializer_class = SiteConfigurationSerializer
    permission_classes = [IsSuperUser]

    def get_object(self):
        return SiteConfiguration.get_solo()


class AdminDashboardView(APIView):
    """The admin panel's Dashboard landing page — one aggregated read of operational/catalogue
    health, entirely from data this app already has. Deliberately excludes anything
    orders/payments/shipping/revenue-shaped: those systems don't exist yet (see TODO.md), and a
    dashboard number implying they do would be worse than not showing one. Every count below is a
    single SQL aggregate/EXISTS query — no per-row Python loop over the full catalogue, no N+1 —
    so this stays cheap regardless of catalogue size. Never returns per-user identifying detail,
    only a total count."""

    permission_classes = [IsSuperUser]

    def get(self, request):
        from django.db.models import Count as DjCount

        from apps.cart.models import Cart
        from apps.generator.models import GeneratedPrintFile, GenerationRequest, MockupRender, ProductVariant
        from apps.printify.models import PrintifySyncRun
        from apps.shop.models import Product
        from apps.shop.services import (
            READINESS_NEEDS_ATTENTION,
            annotate_product_readiness,
            product_readiness_issue_filter,
        )

        User = get_user_model()

        products_needing_attention = (
            annotate_product_readiness(Product.objects.filter(is_active=True))
            .filter(product_readiness_issue_filter())
            .count()
        )

        sellable_variants = ProductVariant.objects.filter(
            is_available=True,
            base_cost__isnull=False,
            template_id=F("product__mockup_template_id"),
        ).filter(Q(external_provider="") | ~Q(external_variant_id="")).count()

        current_carts = Cart.objects.annotate(_item_count=DjCount("items")).filter(_item_count__gt=0).count()

        return Response(
            {
                "products": {
                    "active": Product.objects.filter(is_active=True).count(),
                    "inactive": Product.objects.filter(is_active=False).count(),
                    "needs_attention": products_needing_attention,
                },
                "variants": {
                    "sellable": sellable_variants,
                    "missing_cost": ProductVariant.objects.filter(base_cost__isnull=True).count(),
                    "unavailable": ProductVariant.objects.filter(is_available=False).count(),
                },
                "printify": {
                    "failed_sync_runs": PrintifySyncRun.objects.filter(status=PrintifySyncRun.Status.FAILED).count(),
                },
                "generator": {
                    "failed_mockup_renders": MockupRender.objects.filter(status=MockupRender.Status.FAILED).count(),
                    "failed_generation_requests": GenerationRequest.objects.filter(
                        status=GenerationRequest.Status.FAILED
                    ).count(),
                    "print_files": GeneratedPrintFile.objects.count(),
                },
                "commerce": {
                    "current_carts": current_carts,
                },
                "users": {
                    "total": User.objects.count(),
                },
            }
        )
