import secrets
from django.conf import settings
from django.core import signing
from rest_framework import status
from rest_framework.generics import CreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SiteConfiguration
from .serializers import RegisterSerializer, UserSerializer


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
