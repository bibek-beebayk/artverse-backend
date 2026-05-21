from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="artwork",
            name="thumbnail",
            field=models.ImageField(blank=True, null=True, upload_to="artworks/thumbnails/"),
        ),
    ]
