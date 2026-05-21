from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="thumbnail",
            field=models.ImageField(blank=True, null=True, upload_to="products/thumbnails/"),
        ),
    ]
