import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generator", "0012_mockuptemplate_canvas_height_and_more"),
        ("shop", "0002_product_thumbnail"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="mockup_template",
            field=models.ForeignKey(
                blank=True,
                help_text="The customizable mockup template used to render this product's previews and print files.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shop_products",
                to="generator.mockuptemplate",
            ),
        ),
    ]
