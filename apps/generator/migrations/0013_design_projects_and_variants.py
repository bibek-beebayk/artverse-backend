import django.db.models.deletion
from django.db import migrations, models


def copy_template_to_mockup_template(apps, schema_editor):
    DesignProject = apps.get_model("generator", "DesignProject")
    for project in DesignProject.objects.filter(mockup_template__isnull=True, template__isnull=False):
        project.mockup_template_id = project.template_id
        project.save(update_fields=["mockup_template"])


def noop(apps, schema_editor):
    pass


def remap_design_project_status(apps, schema_editor):
    DesignProject = apps.get_model("generator", "DesignProject")
    DesignProject.objects.filter(status="saved").update(status="draft")
    DesignProject.objects.filter(status="ordered").update(status="ready")


class Migration(migrations.Migration):

    dependencies = [
        ("gallery", "0001_initial"),
        ("shop", "0003_product_mockup_template"),
        ("generator", "0012_mockuptemplate_canvas_height_and_more"),
    ]

    operations = [
        # --- ProductVariant: link to shop.Product, rename/add fields for the richer API shape ---
        migrations.AlterUniqueTogether(
            name="productvariant",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="template",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="variants", to="generator.mockuptemplate"
            ),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="variants",
                to="shop.product",
            ),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="sku",
            field=models.CharField(blank=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productvariant",
            name="name",
            field=models.CharField(
                blank=True, default="", help_text="Display name for this variant, e.g. 'Midnight Black / M'.", max_length=255
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productvariant",
            name="color_name",
            field=models.CharField(blank=True, default="", max_length=120),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="colour",
        ),
        migrations.AddField(
            model_name="productvariant",
            name="color_hex",
            field=models.CharField(blank=True, default="", help_text="e.g. #1a1a1a", max_length=7),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productvariant",
            name="external_provider",
            field=models.CharField(blank=True, default="", help_text="Fulfilment provider name, e.g. Printify.", max_length=120),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="print_provider",
        ),
        migrations.AddField(
            model_name="productvariant",
            name="external_variant_id",
            field=models.CharField(blank=True, default="", help_text="Provider-side variant ID.", max_length=64),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="printify_variant_id",
        ),
        migrations.AddField(
            model_name="productvariant",
            name="inventory",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name="productvariant",
            options={"ordering": ("template", "color_name", "size")},
        ),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.UniqueConstraint(fields=("template", "color_name", "size"), name="unique_template_color_size"),
        ),
        # --- DesignProject: product link, richer source/status/metadata fields, indexes ---
        migrations.AddField(
            model_name="designproject",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_projects",
                to="shop.product",
            ),
        ),
        migrations.AddField(
            model_name="designproject",
            name="mockup_template",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="design_projects",
                to="generator.mockuptemplate",
            ),
        ),
        migrations.RunPython(copy_template_to_mockup_template, noop),
        migrations.RemoveField(
            model_name="designproject",
            name="template",
        ),
        migrations.AlterField(
            model_name="designproject",
            name="mockup_template",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="design_projects", to="generator.mockuptemplate"
            ),
        ),
        migrations.AddField(
            model_name="designproject",
            name="selected_color",
            field=models.CharField(blank=True, default="", help_text="Snapshot of the chosen colour at save time.", max_length=120),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="designproject",
            name="selected_colour",
        ),
        migrations.AddField(
            model_name="designproject",
            name="selected_size",
            field=models.CharField(blank=True, default="", help_text="Snapshot of the chosen size at save time.", max_length=120),
            preserve_default=False,
        ),
        migrations.RunPython(remap_design_project_status, noop),
        migrations.AlterField(
            model_name="designproject",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("ready", "Ready"), ("archived", "Archived")], default="draft", max_length=20
            ),
        ),
        migrations.AddField(
            model_name="designproject",
            name="source_artwork",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_projects",
                to="gallery.artwork",
                help_text="The primary design this project started from, if any.",
            ),
        ),
        migrations.AddField(
            model_name="designproject",
            name="source_generated_image",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_projects",
                to="generator.generatedimage",
            ),
        ),
        migrations.AddField(
            model_name="designproject",
            name="source_image_url",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="designproject",
            name="source_prompt",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="designproject",
            name="thumbnail_url",
            field=models.TextField(
                blank=True, default="", help_text="URL of an already-rendered preview to use as the thumbnail. Never store base64 data here."
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="designproject",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name="designproject",
            index=models.Index(fields=["user", "updated_at"], name="designproj_user_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="designproject",
            index=models.Index(fields=["user", "status"], name="designproj_user_status_idx"),
        ),
        migrations.AddIndex(
            model_name="designproject",
            index=models.Index(fields=["product"], name="designproj_product_idx"),
        ),
        migrations.AddIndex(
            model_name="designproject",
            index=models.Index(fields=["mockup_template"], name="designproj_template_idx"),
        ),
        # --- DesignPlacement: template_part/preview_render links, split crop fields, fit, metadata ---
        migrations.AlterUniqueTogether(
            name="designplacement",
            unique_together=set(),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="template_part",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_placements",
                to="generator.mockuptemplatepart",
                help_text="The exact template part this placement targets, when the template has parts configured.",
            ),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="source_artwork",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_placements",
                to="gallery.artwork",
            ),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="artwork",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="source_generated_image",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_placements",
                to="generator.generatedimage",
            ),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="generated_image",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="source_image_url",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="designplacement",
            name="source_prompt",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="designplacement",
            name="part_name",
            field=models.CharField(
                choices=[("front", "Front"), ("back", "Back"), ("left_sleeve", "Left Sleeve"), ("right_sleeve", "Right Sleeve")],
                default="front",
                max_length=30,
            ),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="product_part",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="x",
            field=models.FloatField(default=0),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="x_position",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="y",
            field=models.FloatField(default=0),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="y_position",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="fit",
            field=models.CharField(choices=[("contain", "Contain"), ("cover", "Cover")], default="contain", max_length=10),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="crop_left",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="crop_top",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="crop_width",
            field=models.FloatField(default=100),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="crop_height",
            field=models.FloatField(default=100),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="crop_data",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="text_elements",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RemoveField(
            model_name="designplacement",
            name="text_settings",
        ),
        migrations.AddField(
            model_name="designplacement",
            name="preview_render",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="design_placements",
                to="generator.mockuprender",
            ),
        ),
        migrations.AddField(
            model_name="designplacement",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterModelOptions(
            name="designplacement",
            options={"ordering": ("design_project", "part_name")},
        ),
        migrations.AddConstraint(
            model_name="designplacement",
            constraint=models.UniqueConstraint(fields=("design_project", "part_name"), name="unique_design_project_part"),
        ),
    ]
