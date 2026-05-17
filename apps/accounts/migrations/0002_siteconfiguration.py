from django.db import migrations, models


def create_default_site_configuration(apps, schema_editor):
    SiteConfiguration = apps.get_model("accounts", "SiteConfiguration")
    SiteConfiguration.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("maintenance_mode", models.BooleanField(default=False)),
                ("maintenance_access_key", models.CharField(blank=True, max_length=255)),
                (
                    "maintenance_message",
                    models.CharField(
                        default="Artverse is currently offline for updates, polishing, and launch preparation. We will be back soon.",
                        max_length=255,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Site Configuration",
                "verbose_name_plural": "Site Configuration",
            },
        ),
        migrations.RunPython(create_default_site_configuration, migrations.RunPython.noop),
    ]
