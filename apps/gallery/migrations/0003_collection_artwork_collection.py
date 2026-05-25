from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0002_artwork_thumbnail"),
    ]

    operations = [
        migrations.CreateModel(
            name="Collection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "ordering": ("name",),
            },
        ),
        migrations.AddField(
            model_name="artwork",
            name="collection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="artworks",
                to="gallery.collection",
            ),
        ),
    ]
