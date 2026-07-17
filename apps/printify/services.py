"""Printify catalogue integration. All Printify HTTP calls go through PrintifyClient here —
never call the Printify API directly from views/admin/management commands, and never expose
PRINTIFY_API_TOKEN to the frontend."""

import time

import requests
from django.conf import settings
from django.utils import timezone

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 15


class PrintifyError(Exception):
    """Base class for all Printify integration errors."""


class PrintifyNotConfiguredError(PrintifyError):
    """Raised when PRINTIFY_API_TOKEN (or, for shop-scoped calls, PRINTIFY_SHOP_ID) is not set."""


class PrintifyAPIError(PrintifyError):
    def __init__(self, message: str, status_code: int | None = None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def is_printify_configured() -> bool:
    return bool(settings.PRINTIFY_API_TOKEN)


class PrintifyClient:
    """Thin authenticated wrapper around the Printify REST API. Retries on 429/5xx with
    exponential backoff (respecting a Retry-After header when present)."""

    def __init__(self, api_token: str | None = None, base_url: str | None = None, shop_id: str | None = None):
        self.api_token = api_token if api_token is not None else settings.PRINTIFY_API_TOKEN
        self.base_url = (base_url if base_url is not None else settings.PRINTIFY_API_BASE_URL).rstrip("/")
        self.shop_id = shop_id if shop_id is not None else settings.PRINTIFY_SHOP_ID
        if not self.api_token:
            raise PrintifyNotConfiguredError(
                "PRINTIFY_API_TOKEN is not set. Add it to your .env to enable catalogue sync."
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "User-Agent": "Artverse/1.0 (+printify-integration)",
                "Accept": "application/json",
            }
        )

    def get(self, path: str, params: dict | None = None) -> dict | list:
        return self._request("GET", path, params=params)

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    raise PrintifyAPIError(f"Network error calling Printify: {exc}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_ATTEMPTS:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 8)
                time.sleep(delay)
                continue

            if not response.ok:
                try:
                    payload = response.json()
                except ValueError:
                    payload = response.text
                raise PrintifyAPIError(
                    f"Printify API returned {response.status_code} for {method} {path}",
                    status_code=response.status_code,
                    payload=payload,
                )

            if not response.content:
                return {}
            return response.json()

        # Unreachable in practice — the loop above always returns or raises — but keeps type
        # checkers happy and fails loudly instead of returning None if it ever is reached.
        raise PrintifyAPIError(f"Printify request failed after {MAX_ATTEMPTS} attempts: {last_error}")


def fetch_blueprints(client: PrintifyClient) -> list[dict]:
    data = client.get("/catalog/blueprints.json")
    return data if isinstance(data, list) else []


def fetch_print_providers(client: PrintifyClient, blueprint_id: int) -> list[dict]:
    """Note: this blueprint-scoped list endpoint returns only `{id, title, decoration_methods}` —
    no location. Use fetch_print_provider_location() (the standalone provider-detail endpoint)
    for that, confirmed against the real API since Printify's docs don't spell out the split."""
    data = client.get(f"/catalog/blueprints/{blueprint_id}/print_providers.json")
    return data if isinstance(data, list) else []


def fetch_print_provider_location(client: PrintifyClient, provider_id: int) -> dict:
    data = client.get(f"/catalog/print_providers/{provider_id}.json")
    return data.get("location", {}) if isinstance(data, dict) else {}


def fetch_print_provider_variants(client: PrintifyClient, blueprint_id: int, provider_id: int) -> dict:
    """Printify's variants endpoint has no boolean "in stock" field on each variant — confirmed
    against the real API. Availability is expressed purely by whether a variant is present at
    all when the `show-out-of-stock` query param is omitted. To get both the complete catalogue
    (for colour/size/placeholder mapping — including currently out-of-stock combos) and per-
    variant availability (for the "disable unavailable variants" sync), this makes two requests
    and folds availability back in as a synthetic `is_enabled` field so the rest of this module
    can just read `variant["is_enabled"]` like an ordinary API field."""
    full_data = client.get(
        f"/catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json",
        params={"show-out-of-stock": 1},
    )
    if not isinstance(full_data, dict):
        return {}

    in_stock_data = client.get(f"/catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json")
    in_stock_ids = (
        {variant.get("id") for variant in in_stock_data.get("variants", [])}
        if isinstance(in_stock_data, dict)
        else set()
    )

    for variant in full_data.get("variants", []):
        variant["is_enabled"] = variant.get("id") in in_stock_ids

    return full_data


def sync_blueprints(triggered_by=None) -> PrintifySyncRun:
    """Pull the full Printify blueprint catalogue and upsert local PrintifyBlueprint rows.
    Existing rows keep their `mockup_template` mapping — only the synced fields are overwritten."""
    run = PrintifySyncRun.objects.create(kind=PrintifySyncRun.Kind.BLUEPRINTS, triggered_by=triggered_by)
    try:
        client = PrintifyClient()
        blueprints = fetch_blueprints(client)

        synced_count = 0
        for entry in blueprints:
            blueprint_id = entry.get("id")
            if blueprint_id is None:
                continue
            PrintifyBlueprint.objects.update_or_create(
                blueprint_id=blueprint_id,
                defaults={
                    "title": entry.get("title", ""),
                    "brand": entry.get("brand", ""),
                    "model": entry.get("model", ""),
                    "description": entry.get("description", ""),
                    "images": entry.get("images", []),
                    "raw_data": entry,
                },
            )
            synced_count += 1

        run.status = PrintifySyncRun.Status.SUCCESS
        run.blueprints_synced = synced_count
    except PrintifyError as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = str(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run


def sync_print_providers_for_blueprint(blueprint: PrintifyBlueprint, triggered_by=None) -> PrintifySyncRun:
    """Pull print providers + their variant/placeholder catalogue for one blueprint and upsert
    local PrintifyPrintProvider rows."""
    run = PrintifySyncRun.objects.create(
        kind=PrintifySyncRun.Kind.PROVIDERS, blueprint=blueprint, triggered_by=triggered_by
    )
    try:
        client = PrintifyClient()
        providers = fetch_print_providers(client, blueprint.blueprint_id)

        provider_count = 0
        variant_count = 0
        for provider_entry in providers:
            provider_id = provider_entry.get("id")
            if provider_id is None:
                continue
            variant_data = fetch_print_provider_variants(client, blueprint.blueprint_id, provider_id)
            variants = variant_data.get("variants", []) if isinstance(variant_data, dict) else []
            location = fetch_print_provider_location(client, provider_id)

            PrintifyPrintProvider.objects.update_or_create(
                blueprint=blueprint,
                provider_id=provider_id,
                defaults={
                    "title": provider_entry.get("title", ""),
                    "location": location,
                    "variants": variants,
                    "raw_data": {"provider": provider_entry, "variants_response": variant_data},
                },
            )
            provider_count += 1
            variant_count += len(variants)

        run.status = PrintifySyncRun.Status.SUCCESS
        run.providers_synced = provider_count
        run.variants_synced = variant_count
    except PrintifyError as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = str(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run


def sync_product_variants_from_printify(product) -> dict:
    """Import/refresh a shop.Product's ProductVariant rows from its mapped Printify print
    provider's synced variant catalogue, matched by (colour, size). Never deletes a row: a
    variant no longer offered by the provider is marked unavailable instead, so existing orders
    and cart references stay valid. Pricing (base_cost/retail_price) is intentionally left for
    an admin to review and set — Printify's catalogue endpoint doesn't reliably return retail-
    ready cost data, so silently writing a guessed number would be worse than leaving it blank.
    Returns a summary dict: {created, updated, marked_unavailable}."""
    from apps.generator.models import ProductVariant

    template = product.mockup_template
    provider = template.selected_print_provider if template else None
    if provider is None:
        raise PrintifyError(
            "This product's mockup template has no mapped Printify print provider — "
            "map a blueprint and select a provider first."
        )

    provider_variants = provider.variants or []
    by_color_size: dict[tuple[str, str], dict] = {}
    for entry in provider_variants:
        options = entry.get("options") or {}
        key = (str(options.get("color", "")).strip(), str(options.get("size", "")).strip())
        by_color_size[key] = entry

    existing = {
        (variant.color_name.strip(), variant.size.strip()): variant
        for variant in ProductVariant.objects.filter(product=product)
    }

    created = updated = marked_unavailable = 0

    for key, entry in by_color_size.items():
        color_name, size = key
        is_enabled = bool(entry.get("is_enabled", True))
        external_variant_id = str(entry.get("id", ""))
        variant = existing.get(key)
        if variant is None:
            ProductVariant.objects.create(
                product=product,
                template=template,
                color_name=color_name,
                size=size,
                name=entry.get("title", "") or f"{color_name} / {size}".strip(" /"),
                external_provider="Printify",
                external_variant_id=external_variant_id,
                is_available=is_enabled,
                supported_print_areas=[p.get("position") for p in entry.get("placeholders", []) if p.get("position")],
            )
            created += 1
        else:
            variant.external_provider = "Printify"
            variant.external_variant_id = external_variant_id
            variant.is_available = is_enabled
            variant.supported_print_areas = [
                p.get("position") for p in entry.get("placeholders", []) if p.get("position")
            ]
            variant.save(update_fields=["external_provider", "external_variant_id", "is_available", "supported_print_areas"])
            updated += 1

    # Anything local that the provider no longer lists gets disabled, not deleted.
    for key, variant in existing.items():
        if key not in by_color_size and variant.is_available:
            variant.is_available = False
            variant.save(update_fields=["is_available"])
            marked_unavailable += 1

    return {"created": created, "updated": updated, "marked_unavailable": marked_unavailable}
