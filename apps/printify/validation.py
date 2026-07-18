"""Cross-cutting validation for Printify mappings — provider/blueprint/placeholder consistency.
Shared by MockupTemplate/MockupTemplatePart model validation (apps/generator/models.py), the
Django admin forms, and the variant-sync service, so there's exactly one place that knows these
rules rather than three copies that could drift.

These are plain Python validators (raise ValueError), not Django ValidationError — callers that
need a ValidationError (model .clean(), admin forms) wrap the call; callers that need a
PrintifyError (the sync service) wrap it differently. Keeping this file free of both Django's
forms machinery and this app's PrintifyError hierarchy is what lets both sides reuse it."""


def get_mapped_blueprint_ids(mockup_template_id) -> set[int]:
    """Blueprint(s) currently mapped to this MockupTemplate (by id, to avoid a second query for
    each caller). Empty if the template is unsaved or nothing is mapped yet — callers should
    treat "nothing mapped" as "no constraint to check yet", not as a validation failure, since
    blueprint mapping is a separate, earlier admin step from selecting a provider."""
    if not mockup_template_id:
        return set()
    from .models import PrintifyBlueprint

    return set(PrintifyBlueprint.objects.filter(mockup_template_id=mockup_template_id).values_list("id", flat=True))


def validate_provider_matches_template_blueprint(mockup_template_id, provider) -> None:
    """Raise ValueError if `provider` doesn't belong to a Printify blueprint mapped to this
    template. No-op (allowed) if the template has no mapped blueprint yet, or `provider` is None
    — see get_mapped_blueprint_ids for why "nothing mapped" isn't itself a failure."""
    if provider is None:
        return
    mapped_blueprint_ids = get_mapped_blueprint_ids(mockup_template_id)
    if mapped_blueprint_ids and provider.blueprint_id not in mapped_blueprint_ids:
        raise ValueError(
            f"Print provider '{provider.title}' belongs to blueprint '{provider.blueprint.title}', "
            "which is not mapped to this mockup template. Select a provider from the mapped blueprint instead."
        )


def get_provider_placeholder_positions(provider) -> set[str]:
    """The set of placeholder position keys actually offered by this provider's synced variant
    catalogue (e.g. {'front', 'back', 'left_sleeve'} — not a fixed list, since a provider's real
    payload may include positions like 'neck' or 'inside_label' that aren't in our own
    front/back/sleeve part names). Tolerates missing/malformed variant data — a provider that
    hasn't been synced yet, or whose payload is missing `placeholders` on some variants, just
    yields an empty (or partial) set rather than raising."""
    if provider is None:
        return set()
    positions: set[str] = set()
    for variant in provider.variants or []:
        if not isinstance(variant, dict):
            continue
        for placeholder in variant.get("placeholders") or []:
            if not isinstance(placeholder, dict):
                continue
            position = placeholder.get("position")
            if position:
                positions.add(position)
    return positions


def validate_placeholder_position(position: str, provider) -> None:
    """Raise ValueError if `position` isn't a placeholder position offered by `provider`'s
    synced variant catalogue. A blank position is always valid — callers should skip calling
    this for an unmapped template part rather than passing an empty string. No-op if `provider`
    is None (nothing to validate against yet) or the provider hasn't been synced yet (an empty
    available set means "unknown", not "definitely invalid" — don't block on stale/no data)."""
    if not position:
        return
    if provider is None:
        return
    available = get_provider_placeholder_positions(provider)
    if available and position not in available:
        raise ValueError(
            f"'{position}' is not a placeholder position offered by print provider '{provider.title}' "
            f"(available: {', '.join(sorted(available))})."
        )
