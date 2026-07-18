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
    template — including when *no* blueprint is mapped at all. A selected provider always
    implies a specific blueprint relationship; there's no such thing as a provider that's valid
    "in general" independent of which blueprint(s) this template is actually mapped to. No-op
    (allowed) only if `provider` itself is None — nothing to check yet."""
    if provider is None:
        return
    mapped_blueprint_ids = get_mapped_blueprint_ids(mockup_template_id)
    if provider.blueprint_id not in mapped_blueprint_ids:
        raise ValueError(
            f"Print provider '{provider.title}' belongs to blueprint '{provider.blueprint.title}', "
            "which is not mapped to this mockup template. Map that blueprint to this template first, "
            "or select a provider from a blueprint that's already mapped."
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


def provider_has_synced_placeholder_data(provider) -> bool:
    """True if this provider's synced variant catalogue actually contains placeholder data to
    validate a mapping against. False for a provider with no variants at all, variants with no
    `placeholders`, or malformed/empty data — whichever it is, there's nothing to confirm a
    mapping against, so validate_placeholder_position treats it as "can't verify" rather than
    "anything goes." (A PrintifyPrintProvider row only ever exists after a successful sync — see
    sync_print_providers_for_blueprint — so this is really "synced with usable data" vs. "synced
    but empty/malformed"; there's no separate "never synced" row state to track.)"""
    return bool(get_provider_placeholder_positions(provider))


def validate_placeholder_position(position: str, provider) -> None:
    """Raise ValueError if `position` isn't a placeholder position offered by `provider`'s
    synced variant catalogue. A blank position is always valid — an unmapped template part, and
    callers should still prefer skipping the call entirely for that case. A *non-blank* position
    requires both a selected provider and that provider to have usable synced placeholder data —
    "we don't know yet" is deliberately treated as invalid, not as a free pass: silently accepting
    an unverifiable mapping is worse than asking the admin to select/sync a provider first."""
    if not position:
        return
    if provider is None:
        raise ValueError(
            "An explicit placeholder mapping requires a Printify print provider to be selected on this template first."
        )
    if not provider_has_synced_placeholder_data(provider):
        raise ValueError(
            "This provider's variants and print areas have not been synchronized yet. "
            "Synchronize the provider before mapping template placeholders."
        )
    available = get_provider_placeholder_positions(provider)
    if position not in available:
        raise ValueError(
            f"'{position}' is not a placeholder position offered by print provider '{provider.title}' "
            f"(available: {', '.join(sorted(available))})."
        )


def validate_template_placeholder_mappings(template) -> dict[str, list[str]]:
    """Check every part on `template` with a non-blank printify_placeholder_position against
    template.selected_print_provider, via validate_placeholder_position (so the same rules apply
    here as everywhere else). Returns {part_name: [error, ...]} for every failing part — an empty
    dict means everything's consistent. Doesn't raise itself; callers decide how to report this
    alongside other validation (see validate_blueprint_mapping)."""
    errors: dict[str, list[str]] = {}
    provider = template.selected_print_provider
    for part in template.parts.all():
        if not part.printify_placeholder_position:
            continue
        try:
            validate_placeholder_position(part.printify_placeholder_position, provider)
        except ValueError as exc:
            errors[part.name] = [str(exc)]
    return errors


class MappingValidationError(ValueError):
    """Raised by validate_blueprint_mapping() — carries either just a message (provider
    mismatch) or a message plus a per-part errors dict (placeholder mismatches), matching
    whichever shape the caller needs for its 400 response."""

    def __init__(self, message: str, part_errors: dict[str, list[str]] | None = None):
        super().__init__(message)
        self.part_errors = part_errors or {}


def validate_blueprint_mapping(template) -> None:
    """Full consistency check for a blueprint→template mapping. Call this *after* tentatively
    saving the new `blueprint.mockup_template` within an open transaction — get_mapped_blueprint_ids
    (used by validate_provider_matches_template_blueprint below) needs to see the pending mapping
    via read-your-writes to know whether the template's existing selected_print_provider is still
    consistent with it. No-op if `template` is None (unmapping — nothing to conflict with).

    Checks provider-blueprint consistency first and stops there if it fails — per-part placeholder
    errors would be meaningless against a provider that doesn't even belong to the right blueprint.
    Only checks placeholders once the provider itself checks out. Raises MappingValidationError;
    callers should run this inside the same transaction.atomic() as the tentative save so a
    failure here rolls back that save too, leaving nothing partially applied."""
    if template is None:
        return

    try:
        validate_provider_matches_template_blueprint(template.pk, template.selected_print_provider)
    except ValueError:
        raise MappingValidationError(
            "The selected mockup template is configured with a print provider from another "
            "Printify blueprint. Clear or change the selected provider before remapping this blueprint."
        )

    part_errors = validate_template_placeholder_mappings(template)
    if part_errors:
        raise MappingValidationError(
            "The template contains invalid Printify placeholder mappings.", part_errors=part_errors
        )
