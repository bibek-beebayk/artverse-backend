"""Removes MockupTemplate's root-level "bypass Parts entirely" fields (base_image/mask_image/
displacement_map/shadow_layer/highlight_layer/config/canvas_width/canvas_height) — every
renderable surface must be a MockupTemplatePart from here on (apps.generator.services.
render_mockup_to_image no longer falls back to template.* at all). Also flips is_active's default
to False, matching apps.shop.models.Product: a template isn't usable until it has at least one
part, so there's nothing to "activate" the moment it's created. Depends on 0020's preflight check
that no template currently has zero parts. See CHANGELOG.md for the full rationale."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('generator', '0020_check_all_templates_have_parts'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='base_image',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='canvas_height',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='canvas_width',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='config',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='displacement_map',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='highlight_layer',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='mask_image',
        ),
        migrations.RemoveField(
            model_name='mockuptemplate',
            name='shadow_layer',
        ),
        migrations.AlterField(
            model_name='mockuptemplate',
            name='is_active',
            field=models.BooleanField(default=False),
        ),
    ]
