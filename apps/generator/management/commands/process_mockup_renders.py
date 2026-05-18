from django.core.management.base import BaseCommand

from apps.generator.models import MockupRender
from apps.generator.services import process_mockup_render


class Command(BaseCommand):
    help = "Process pending or failed mockup renders."

    def add_arguments(self, parser):
        parser.add_argument("--render-id", type=int, help="Process a single render by ID.")
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Include failed renders in the processing run.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Maximum number of renders to process.",
        )

    def handle(self, *args, **options):
        queryset = MockupRender.objects.select_related("template", "generated_image")

        render_id = options.get("render_id")
        if render_id:
            queryset = queryset.filter(pk=render_id)
        else:
            statuses = [MockupRender.Status.PENDING]
            if options.get("retry_failed"):
                statuses.append(MockupRender.Status.FAILED)
            queryset = queryset.filter(status__in=statuses)[: options["limit"]]

        renders = list(queryset)
        if not renders:
            self.stdout.write(self.style.WARNING("No mockup renders matched the requested criteria."))
            return

        for render in renders:
            processed = process_mockup_render(render)
            if processed.status == MockupRender.Status.READY:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Render {processed.pk} processed successfully for template '{processed.template.name}'."
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Render {processed.pk} failed: {processed.error_message or 'Unknown error'}"
                    )
                )
