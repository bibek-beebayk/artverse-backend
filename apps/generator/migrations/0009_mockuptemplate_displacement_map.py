from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generator", "0008_mockuprender_crop_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="mockuptemplate",
            name="displacement_map",
            field=models.ImageField(blank=True, null=True, upload_to="mockup-templates/displacement/"),
        ),
    ]
