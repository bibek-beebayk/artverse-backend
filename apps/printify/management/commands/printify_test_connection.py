from django.core.management.base import BaseCommand, CommandError

from apps.printify.services import PrintifyError, validate_configured_shop


class Command(BaseCommand):
    help = (
        "Verify the configured Printify API token and shop ID are actually valid and "
        "accessible — not just present in settings. Never prints the token."
    )

    def handle(self, *args, **options):
        try:
            shop = validate_configured_shop()
        except PrintifyError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Printify connection successful."))
        self.stdout.write(f"Shop: {shop['title']}")
        self.stdout.write(f"Shop ID: {shop['id']}")
        self.stdout.write(f"Sales channel: {shop['sales_channel']}")
