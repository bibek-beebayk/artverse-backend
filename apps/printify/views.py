from django.db import transaction
from django.db.models import Count
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from apps.accounts.permissions import IsSuperUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PrintifyBlueprint, PrintifySyncRun
from .serializers import (
    PrintifyBlueprintDetailSerializer,
    PrintifyBlueprintListSerializer,
    PrintifyBlueprintMapSerializer,
    PrintifySyncRunSerializer,
)
from .services import PrintifyError, is_printify_configured, sync_blueprints, sync_print_providers_for_blueprint, validate_configured_shop
from .validation import MappingValidationError, validate_blueprint_mapping


class PrintifyConnectionStatusView(APIView):
    """Superuser-only: is Printify configured, and is it *actually* reachable right now? A token
    string being present in settings isn't the same as a working connection — this performs a
    real (but cheap, single-request) shop lookup so "connected" reflects reality, not just
    "someone typed a token in once". Always 200 — a disconnected integration is a normal admin-
    UI state to display, not a server error.

    Every view in this module was tightened from IsAdminUser (is_staff) to IsSuperUser once the
    React admin panel's Printify pages became the primary consumer — the panel itself is already
    superuser-gated, and a plain staff account hitting these endpoints directly (bypassing the
    frontend route guard) previously got a 200. Never echoes PRINTIFY_API_TOKEN or any other
    secret back to the client."""

    permission_classes = [IsSuperUser]

    def get(self, request):
        last_run = PrintifySyncRun.objects.order_by("-started_at").first()

        configured = is_printify_configured()
        connected = False
        shop = None
        error = None

        if not is_printify_configured():
            error = "PRINTIFY_API_TOKEN is not configured (or PRINTIFY_ENABLED is false)."
        else:
            try:
                shop = validate_configured_shop()
                connected = True
            except PrintifyError as exc:
                error = str(exc)

        return Response(
            {
                "configured": configured,
                "connected": connected,
                "shop": shop,
                "error": error,
                "blueprint_count": PrintifyBlueprint.objects.count(),
                "mapped_blueprint_count": PrintifyBlueprint.objects.filter(mockup_template__isnull=False).count(),
                "last_sync_run": PrintifySyncRunSerializer(last_run).data if last_run else None,
            },
            status=status.HTTP_200_OK,
        )


class PrintifyBlueprintListView(ListAPIView):
    serializer_class = PrintifyBlueprintListSerializer
    permission_classes = [IsSuperUser]

    def get_queryset(self):
        queryset = PrintifyBlueprint.objects.annotate(provider_count_value=Count("print_providers", distinct=True))
        is_mapped = self.request.query_params.get("is_mapped")
        if is_mapped == "true":
            queryset = queryset.filter(mockup_template__isnull=False)
        elif is_mapped == "false":
            queryset = queryset.filter(mockup_template__isnull=True)
        # Reverse lookup for the Mockup Template edit screen's "Printify Mapping" section: given
        # a template id, find the blueprint (if any) mapped to it — PrintifyBlueprint.mockup_template
        # has no other query surface for this today.
        mockup_template_id = self.request.query_params.get("mockup_template")
        if mockup_template_id:
            queryset = queryset.filter(mockup_template_id=mockup_template_id)
        return queryset


class PrintifyBlueprintDetailView(RetrieveAPIView):
    queryset = PrintifyBlueprint.objects.prefetch_related("print_providers").annotate(
        provider_count_value=Count("print_providers", distinct=True)
    )
    serializer_class = PrintifyBlueprintDetailSerializer
    permission_classes = [IsSuperUser]


class PrintifyBlueprintMapView(APIView):
    """Link (or unlink, with mockup_template_id: null) a synced blueprint to an internal
    MockupTemplate — the "map internal products to Printify" step. Enforces one blueprint per
    template: mapping a new blueprint to a template that already has a *different* blueprint
    mapped to it clears that old mapping first (in the same transaction) — otherwise the old
    blueprint would still count as "mapped" for consistency purposes, and a provider belonging
    to it could slip past validation even though it no longer matches the template's new
    blueprint.

    A blueprint can't be mapped to a template whose existing selected_print_provider belongs to
    a *different* blueprint (that would leave the template pointing at a provider it can no
    longer reach), and can't be mapped if that would leave any of the template's explicit
    placeholder mappings unsupported by the (unchanged) selected provider. All of this — the
    auto-unmap, the tentative save, and both checks — runs inside one transaction.atomic(): a
    failure rolls back everything, so nothing is left partially mapped and the previous
    mapping/provider/placeholder configuration is untouched, not silently cleared."""

    permission_classes = [IsSuperUser]

    def post(self, request, pk):
        try:
            blueprint = PrintifyBlueprint.objects.get(pk=pk)
        except PrintifyBlueprint.DoesNotExist:
            return Response({"detail": "Blueprint not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PrintifyBlueprintMapSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_template_id = serializer.validated_data["mockup_template_id"]

        try:
            with transaction.atomic():
                if new_template_id:
                    PrintifyBlueprint.objects.filter(mockup_template_id=new_template_id).exclude(pk=blueprint.pk).update(
                        mockup_template=None
                    )
                blueprint.mockup_template_id = new_template_id
                blueprint.save(update_fields=["mockup_template"])

                template = None
                if new_template_id:
                    from apps.generator.models import MockupTemplate

                    template = MockupTemplate.objects.select_related("selected_print_provider__blueprint").get(
                        pk=new_template_id
                    )
                validate_blueprint_mapping(template)
        except MappingValidationError as exc:
            body = {"detail": str(exc)}
            if exc.part_errors:
                body["errors"] = exc.part_errors
            return Response(body, status=status.HTTP_400_BAD_REQUEST)

        return Response(PrintifyBlueprintDetailSerializer(blueprint).data)


class PrintifySyncBlueprintsView(APIView):
    """Trigger a full blueprint-catalogue sync. Runs synchronously — there is no task queue yet
    (that's a later roadmap section), so this can take a few seconds for a large catalogue.

    Validates the configured shop first, same as the sync_printify_catalogue management command
    and unconditionally (no dev-only bypass here — this is a production admin API, not a local
    CLI) — a misconfigured PRINTIFY_SHOP_ID wouldn't actually break the blueprint sync itself
    (that endpoint isn't shop-scoped), but letting an admin trigger sync after sync without ever
    surfacing that the configured shop is wrong would be its own kind of silent bypass."""

    permission_classes = [IsSuperUser]

    def post(self, request):
        try:
            validate_configured_shop()
        except PrintifyError as exc:
            return Response({"detail": f"Printify connection check failed: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        run = sync_blueprints(triggered_by=request.user)
        response_status = status.HTTP_200_OK if run.status == PrintifySyncRun.Status.SUCCESS else status.HTTP_502_BAD_GATEWAY
        return Response(PrintifySyncRunSerializer(run).data, status=response_status)


class PrintifySyncProvidersView(APIView):
    """Trigger a print-provider + variant sync for one already-synced blueprint. Validates the
    configured shop first — see PrintifySyncBlueprintsView's docstring for why."""

    permission_classes = [IsSuperUser]

    def post(self, request, pk):
        try:
            blueprint = PrintifyBlueprint.objects.get(pk=pk)
        except PrintifyBlueprint.DoesNotExist:
            return Response({"detail": "Blueprint not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            validate_configured_shop()
        except PrintifyError as exc:
            return Response({"detail": f"Printify connection check failed: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        run = sync_print_providers_for_blueprint(blueprint, triggered_by=request.user)
        response_status = status.HTTP_200_OK if run.status == PrintifySyncRun.Status.SUCCESS else status.HTTP_502_BAD_GATEWAY
        return Response(PrintifySyncRunSerializer(run).data, status=response_status)


class PrintifySyncRunListView(ListAPIView):
    """Admin panel's Printify > Sync Runs audit screen — previously only the single most-recent
    run was surfaced (via PrintifyConnectionStatusView.last_sync_run); this exposes the full
    history, read-only, same permission as every other Printify admin endpoint."""

    queryset = PrintifySyncRun.objects.select_related("triggered_by").order_by("-started_at")
    serializer_class = PrintifySyncRunSerializer
    permission_classes = [IsSuperUser]
