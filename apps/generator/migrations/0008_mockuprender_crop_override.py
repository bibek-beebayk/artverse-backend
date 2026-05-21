from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generator", "0007_alter_mockuprender_output_image_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="mockuprender",
            name="crop_override",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
