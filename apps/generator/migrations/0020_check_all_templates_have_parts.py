"""Defensive preflight for 0021_mockuptemplate_remove_root_fields_and_more: refuses to proceed
if any MockupTemplate rows exist with zero MockupTemplatePart rows. That migration removes
MockupTemplate's root-level base_image/mask_image/displacement_map/shadow_layer/highlight_layer/
config/canvas_width/canvas_height fields — every renderable surface is a MockupTemplatePart from
here on, so a template with no parts at all would silently lose whatever root image data it had,
with no replacement. This repo's own dev database has zero such templates as of this migration
being written (verified via the shell preflight documented in CHANGELOG.md), but this migration
exists so *any* environment applying it gets a clear failure and a documented manual-fix path
(add at least one MockupTemplatePart to the listed template(s) via the Django admin or the
in-app admin panel's Parts tab, using the template's existing base_image/mask_image/etc as the
new part's images) instead of a silent, unannounced field removal.
"""

from django.db import migrations
from django.db.models import Count


def check_all_templates_have_parts(apps, schema_editor):
    MockupTemplate = apps.get_model("generator", "MockupTemplate")
    orphans = list(
        MockupTemplate.objects.annotate(part_count=Count("parts"))
        .filter(part_count=0)
        .values_list("id", "slug")
    )
    if orphans:
        listed = ", ".join(f"{pk} ({slug})" for pk, slug in orphans)
        raise RuntimeError(
            "Cannot remove MockupTemplate's root-level image/config fields: "
            f"{len(orphans)} template(s) with zero MockupTemplatePart rows exist ({listed}). "
            "Add at least one part to each (Django admin: Generator -> Mockup templates -> "
            "Parts inline; in-app admin panel: Catalog -> Mockup Templates -> select the "
            "template -> Parts tab) before this migration can be re-run — using the template's "
            "current base_image/mask_image/displacement_map/shadow_layer/highlight_layer as "
            "that part's images preserves the existing render output exactly."
        )


def noop_reverse(apps, schema_editor):
    # Nothing to undo — this migration only ever reads data, never writes it.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("generator", "0019_designplacement_source_asset_and_more"),
    ]

    operations = [
        migrations.RunPython(check_all_templates_have_parts, noop_reverse),
    ]
