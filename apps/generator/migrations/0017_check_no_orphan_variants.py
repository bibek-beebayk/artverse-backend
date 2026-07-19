"""Defensive preflight for 0018_productvariant_product_required_and_more: refuses to proceed
(raising a clear, actionable error instead of a raw NOT NULL constraint violation) if any
ProductVariant rows exist with no product. This repo's own dev database has zero orphans as of
this migration being written (verified via the shell preflight documented in CHANGELOG.md), but
this migration exists so *any* environment applying it gets a clear failure and a documented
manual-cleanup path instead of an opaque database error, or worse, silently deleting/reassigning
data. There is deliberately no automatic remediation here — an orphan variant has no
deterministic "correct" product to attach it to, so mapping it automatically would risk
attaching real pricing/availability data to the wrong storefront listing. See CHANGELOG.md for
the manual cleanup procedure if this ever actually raises."""

from django.db import migrations


def check_no_orphan_variants(apps, schema_editor):
    ProductVariant = apps.get_model("generator", "ProductVariant")
    orphans = list(ProductVariant.objects.filter(product__isnull=True).values_list("id", flat=True))
    if orphans:
        raise RuntimeError(
            "Cannot make ProductVariant.product required: "
            f"{len(orphans)} orphan variant(s) with no product exist (ids: {orphans}). "
            "Each one needs a product assigned manually (there is no deterministic way to infer "
            "the correct product from provider/variant data alone) before this migration can be "
            "re-run. Do not delete these rows to work around this check — external "
            "provider/variant IDs and any historical references may depend on them. See "
            "CHANGELOG.md for the documented cleanup procedure."
        )


def noop_reverse(apps, schema_editor):
    # Nothing to undo — this migration only ever reads data, never writes it.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("generator", "0016_generatedprintfile"),
    ]

    operations = [
        migrations.RunPython(check_no_orphan_variants, noop_reverse),
    ]
