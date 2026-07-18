from django.conf import settings
from django.db import models


class PrintifyBlueprint(models.Model):
    """A synced snapshot of one Printify catalogue blueprint (product type), e.g. "Unisex
    Heavy Cotton Tee". `raw_data` keeps the full API response so nothing is lost even if the
    parsed fields below don't cover something a future feature needs."""

    blueprint_id = models.PositiveIntegerField(unique=True, help_text="Printify's numeric blueprint ID.")
    title = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    images = models.JSONField(default=list, blank=True, help_text="Blueprint preview image URLs from Printify.")
    raw_data = models.JSONField(default=dict, blank=True)
    mockup_template = models.ForeignKey(
        "generator.MockupTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="printify_blueprints",
        help_text="Internal mockup template this blueprint is mapped to, if any.",
    )
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("title",)

    def __str__(self) -> str:
        return f"{self.title} (#{self.blueprint_id})"


class PrintifyPrintProvider(models.Model):
    """A print provider offering a blueprint, with its variant catalogue (colours, sizes,
    per-print-area placeholders) as returned by Printify. One blueprint typically has several
    print providers to choose from; `variants` holds the raw list from Printify's
    `.../print_providers/{id}/variants.json` endpoint."""

    blueprint = models.ForeignKey(PrintifyBlueprint, on_delete=models.CASCADE, related_name="print_providers")
    provider_id = models.PositiveIntegerField(help_text="Printify's numeric print provider ID.")
    title = models.CharField(max_length=255)
    location = models.JSONField(default=dict, blank=True, help_text="Provider location/country info from Printify.")
    variants = models.JSONField(
        default=list,
        blank=True,
        help_text="Raw variant list: id, title, options (colour/size), placeholders (print-area position + dimensions) per variant.",
    )
    raw_data = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("blueprint", "title")
        constraints = [
            models.UniqueConstraint(fields=["blueprint", "provider_id"], name="unique_blueprint_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.title} for {self.blueprint.title}"


class PrintifySyncRun(models.Model):
    """Audit record for a catalogue sync — doubles as the "connection status / last sync time"
    the roadmap asks for: the most recent successful run is the connection's last-known-good
    sync, and a recent failed run surfaces the error without anyone needing to check logs."""

    class Kind(models.TextChoices):
        BLUEPRINTS = "blueprints", "Blueprint list"
        PROVIDERS = "providers", "Print providers for one blueprint"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    blueprint = models.ForeignKey(
        PrintifyBlueprint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_runs",
        help_text="Set for a Kind.PROVIDERS run; blank for a full blueprint-list sync.",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="printify_sync_runs"
    )
    blueprints_synced = models.PositiveIntegerField(default=0)
    blueprints_created = models.PositiveIntegerField(default=0)
    blueprints_updated = models.PositiveIntegerField(default=0)
    providers_synced = models.PositiveIntegerField(default=0)
    providers_created = models.PositiveIntegerField(default=0)
    providers_updated = models.PositiveIntegerField(default=0)
    variants_synced = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.get_status_display()} ({self.started_at:%Y-%m-%d %H:%M})"
