from django.utils.text import slugify


def unique_slugify(instance, value: str, *, slug_field_name: str = "slug") -> str:
    """Derive a unique slug for `instance`'s `slug_field_name`, from `value` (typically a name/
    title field), that doesn't collide with any other row of the same model. Appends `-2`, `-3`,
    ... until unique — never overwrites an existing row, never raises on collision. Truncates to
    the field's `max_length` *before* appending a numeric suffix, so the suffixed result never
    exceeds it either. Falls back to the model's own name if `value` has no sluggable characters
    (e.g. an all-emoji title), so this never returns an empty string.

    Callers should only call this when the instance's slug is actually blank — see each model's
    `save()` override — this function itself does not check that."""
    model = instance.__class__
    max_length = model._meta.get_field(slug_field_name).max_length or 50

    base_slug = slugify(value)[:max_length] or slugify(model._meta.verbose_name) or "item"
    slug = base_slug
    queryset = model._default_manager.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    counter = 2
    while queryset.filter(**{slug_field_name: slug}).exists():
        suffix = f"-{counter}"
        slug = f"{base_slug[: max_length - len(suffix)]}{suffix}"
        counter += 1

    return slug
