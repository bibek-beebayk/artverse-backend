from django.core.management.base import BaseCommand, CommandError

from apps.printify.models import PrintifyBlueprint, PrintifySyncRun
from apps.printify.services import sync_blueprints, sync_print_providers_for_blueprint


class Command(BaseCommand):
    help = (
        "Sync the Printify catalogue. With no arguments, syncs the full blueprint list "
        "(the first-run bootstrap — the admin list is empty until this has run once). "
        "Pass --blueprint-id to also sync that blueprint's print providers and variants."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--blueprint-id",
            type=int,
            help="Sync print providers + variants for this already-synced blueprint's Printify blueprint_id "
            "instead of (or in addition to, if also given) the full blueprint list.",
        )
        parser.add_argument(
            "--skip-blueprint-list",
            action="store_true",
            help="With --blueprint-id, skip re-syncing the full blueprint list first.",
        )

    def handle(self, *args, **options):
        blueprint_id = options.get("blueprint_id")

        if not blueprint_id or not options.get("skip_blueprint_list"):
            self.stdout.write("Syncing Printify blueprint list...")
            run = sync_blueprints()
            if run.status != PrintifySyncRun.Status.SUCCESS:
                raise CommandError(run.error_message or "Blueprint sync failed.")
            self.stdout.write(self.style.SUCCESS(f"Synced {run.blueprints_synced} blueprint(s)."))

        if blueprint_id:
            try:
                blueprint = PrintifyBlueprint.objects.get(blueprint_id=blueprint_id)
            except PrintifyBlueprint.DoesNotExist as exc:
                raise CommandError(
                    f"No synced blueprint with blueprint_id={blueprint_id}. Run without --blueprint-id first."
                ) from exc

            self.stdout.write(f"Syncing print providers for '{blueprint.title}'...")
            run = sync_print_providers_for_blueprint(blueprint)
            if run.status != PrintifySyncRun.Status.SUCCESS:
                raise CommandError(run.error_message or "Print provider sync failed.")
            self.stdout.write(
                self.style.SUCCESS(f"Synced {run.providers_synced} provider(s), {run.variants_synced} variant(s).")
            )
