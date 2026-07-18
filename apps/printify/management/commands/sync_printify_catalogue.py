from django.core.management.base import BaseCommand, CommandError

from apps.printify.models import PrintifyBlueprint, PrintifySyncRun
from apps.printify.services import PrintifyError, sync_blueprints, sync_print_providers_for_blueprint, validate_configured_shop


class Command(BaseCommand):
    help = (
        "Sync the Printify catalogue. With no arguments, syncs the full blueprint list "
        "(the first-run bootstrap — the admin list is empty until this has run once). "
        "Pass --blueprint-id to also sync that blueprint's print providers and variants, "
        "optionally narrowed to one provider with --provider-id. Validates the Printify "
        "connection (token + configured shop) before doing anything else."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--blueprint-id",
            type=int,
            help="Sync print providers + variants for this already-synced blueprint's Printify blueprint_id "
            "instead of (or in addition to, if also given) the full blueprint list.",
        )
        parser.add_argument(
            "--providers",
            action="store_true",
            help="Explicit alias confirming provider sync when used with --blueprint-id "
            "(provider sync already happens whenever --blueprint-id is given; this flag "
            "changes nothing, it just makes the invocation self-documenting).",
        )
        parser.add_argument(
            "--provider-id",
            type=int,
            help="With --blueprint-id, sync only this one Printify print-provider ID instead of all "
            "providers offering that blueprint.",
        )
        parser.add_argument(
            "--skip-blueprint-list",
            action="store_true",
            help="With --blueprint-id, skip re-syncing the full blueprint list first.",
        )
        parser.add_argument(
            "--skip-connection-check",
            action="store_true",
            help="Skip the pre-flight connection/shop validation (not recommended — a bad token or "
            "shop ID will otherwise fail fast with a clear message before any sync work starts).",
        )

    def handle(self, *args, **options):
        blueprint_id = options.get("blueprint_id")
        provider_id = options.get("provider_id")

        if provider_id and not blueprint_id:
            raise CommandError("--provider-id requires --blueprint-id.")

        if not options.get("skip_connection_check"):
            self.stdout.write("Validating Printify connection...")
            try:
                shop = validate_configured_shop()
            except PrintifyError as exc:
                raise CommandError(f"Printify connection check failed: {exc}") from exc
            self.stdout.write(self.style.SUCCESS(f"Connected to shop '{shop['title']}' (ID {shop['id']})."))

        if not blueprint_id or not options.get("skip_blueprint_list"):
            self.stdout.write("Syncing Printify blueprint list...")
            run = sync_blueprints()
            if run.status != PrintifySyncRun.Status.SUCCESS:
                raise CommandError(run.error_message or "Blueprint sync failed.")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Blueprints: {run.blueprints_created} created, {run.blueprints_updated} updated "
                    f"({run.blueprints_synced} total)."
                )
            )

        if blueprint_id:
            try:
                blueprint = PrintifyBlueprint.objects.get(blueprint_id=blueprint_id)
            except PrintifyBlueprint.DoesNotExist as exc:
                raise CommandError(
                    f"No synced blueprint with blueprint_id={blueprint_id}. Run without --blueprint-id first."
                ) from exc

            target = f"provider {provider_id}" if provider_id else "all print providers"
            self.stdout.write(f"Syncing {target} for '{blueprint.title}'...")
            run = sync_print_providers_for_blueprint(blueprint, provider_id=provider_id)
            if run.status != PrintifySyncRun.Status.SUCCESS:
                raise CommandError(run.error_message or "Print provider sync failed.")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Providers: {run.providers_created} created, {run.providers_updated} updated "
                    f"({run.providers_synced} total, {run.variants_synced} variant(s))."
                )
            )
