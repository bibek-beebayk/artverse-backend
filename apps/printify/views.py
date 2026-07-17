from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PrintifyBlueprint, PrintifySyncRun
from .serializers import (
    PrintifyBlueprintDetailSerializer,
    PrintifyBlueprintListSerializer,
    PrintifyBlueprintMapSerializer,
    PrintifySyncRunSerializer,
)
from .services import is_printify_configured, sync_blueprints, sync_print_providers_for_blueprint


class PrintifyConnectionStatusView(APIView):
    """Admin-only: is Printify configured, and what happened during the last sync? This is the
    "connection status" the roadmap asks for — the actual credentials live only in env vars."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        last_run = PrintifySyncRun.objects.order_by("-started_at").first()
        return Response(
            {
                "is_configured": is_printify_configured(),
                "blueprint_count": PrintifyBlueprint.objects.count(),
                "mapped_blueprint_count": PrintifyBlueprint.objects.filter(mockup_template__isnull=False).count(),
                "last_sync_run": PrintifySyncRunSerializer(last_run).data if last_run else None,
            }
        )


class PrintifyBlueprintListView(ListAPIView):
    serializer_class = PrintifyBlueprintListSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        queryset = PrintifyBlueprint.objects.all()
        is_mapped = self.request.query_params.get("is_mapped")
        if is_mapped == "true":
            queryset = queryset.filter(mockup_template__isnull=False)
        elif is_mapped == "false":
            queryset = queryset.filter(mockup_template__isnull=True)
        return queryset


class PrintifyBlueprintDetailView(RetrieveAPIView):
    queryset = PrintifyBlueprint.objects.prefetch_related("print_providers")
    serializer_class = PrintifyBlueprintDetailSerializer
    permission_classes = [IsAdminUser]


class PrintifyBlueprintMapView(APIView):
    """Link (or unlink, with mockup_template_id: null) a synced blueprint to an internal
    MockupTemplate — the "map internal products to Printify" step."""

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            blueprint = PrintifyBlueprint.objects.get(pk=pk)
        except PrintifyBlueprint.DoesNotExist:
            return Response({"detail": "Blueprint not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PrintifyBlueprintMapSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blueprint.mockup_template_id = serializer.validated_data["mockup_template_id"]
        blueprint.save(update_fields=["mockup_template"])
        return Response(PrintifyBlueprintDetailSerializer(blueprint).data)


class PrintifySyncBlueprintsView(APIView):
    """Trigger a full blueprint-catalogue sync. Runs synchronously — there is no task queue yet
    (that's a later roadmap section), so this can take a few seconds for a large catalogue."""

    permission_classes = [IsAdminUser]

    def post(self, request):
        run = sync_blueprints(triggered_by=request.user)
        response_status = status.HTTP_200_OK if run.status == PrintifySyncRun.Status.SUCCESS else status.HTTP_502_BAD_GATEWAY
        return Response(PrintifySyncRunSerializer(run).data, status=response_status)


class PrintifySyncProvidersView(APIView):
    """Trigger a print-provider + variant sync for one already-synced blueprint."""

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            blueprint = PrintifyBlueprint.objects.get(pk=pk)
        except PrintifyBlueprint.DoesNotExist:
            return Response({"detail": "Blueprint not found."}, status=status.HTTP_404_NOT_FOUND)

        run = sync_print_providers_for_blueprint(blueprint, triggered_by=request.user)
        response_status = status.HTTP_200_OK if run.status == PrintifySyncRun.Status.SUCCESS else status.HTTP_502_BAD_GATEWAY
        return Response(PrintifySyncRunSerializer(run).data, status=response_status)
