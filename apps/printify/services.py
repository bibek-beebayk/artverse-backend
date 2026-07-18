"""Printify catalogue integration. All Printify HTTP calls go through PrintifyClient here —
never call the Printify API directly from views/admin/management commands, and never expose
PRINTIFY_API_TOKEN to the frontend."""

import time

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PrintifyBlueprint, PrintifyPrintProvider, PrintifySyncRun

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class PrintifyError(Exception):
    """Base class for all Printify integration errors."""


class PrintifyNotConfiguredError(PrintifyError):
    """Raised when Printify isn't usable yet: PRINTIFY_ENABLED is false, or PRINTIFY_API_TOKEN
    (or, for shop-scoped calls, PRINTIFY_SHOP_ID) is not set."""


class PrintifyAPIError(PrintifyError):
    def __init__(self, message: str, status_code: int | None = None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class PrintifyResponseError(PrintifyError):
    """Raised when Printify returns a response in a shape this integration doesn't understand
    (e.g. an object where a list was expected) — distinct from PrintifyAPIError so callers can
    tell "Printify said no" apart from "Printify said something we can't parse"."""


class PrintifyShopNotFoundError(PrintifyError):
    """Raised when the configured PRINTIFY_SHOP_ID doesn't match any shop the configured token
    can access — a token being valid doesn't mean it can see *this* shop."""


def is_printify_configured() -> bool:
    """A token string being present isn't enough to call the integration configured — it must
    also be explicitly turned on via PRINTIFY_ENABLED, a kill switch independent of credentials."""
    return bool(settings.PRINTIFY_ENABLED) and bool(settings.PRINTIFY_API_TOKEN)


def safe_error_message(exc: Exception) -> str:
    """A message safe to persist on a PrintifySyncRun / show an admin — never the exception's
    raw args for unexpected exception types, since those could (in principle) echo back request
    internals. PrintifyError messages are already hand-written and safe; everything else is
    reduced to just its class name plus a generic note."""
    if isinstance(exc, PrintifyError):
        return str(exc)
    return f"Unexpected error during Printify sync: {exc.__class__.__name__}"


class PrintifyClient:
    """Thin authenticated wrapper around the Printify REST API. Retries on 429/5xx with
    exponential backoff (respecting a Retry-After header when present)."""

    def __init__(self, api_token: str | None = None, base_url: str | None = None, shop_id: str | None = None):
        if not settings.PRINTIFY_ENABLED and api_token is None:
            raise PrintifyNotConfiguredError(
                "Printify integration is disabled (PRINTIFY_ENABLED is not set). Enable it in .env to use catalogue sync."
            )
        self.api_token = api_token if api_token is not None else settings.PRINTIFY_API_TOKEN
        self.base_url = (base_url if base_url is not None else settings.PRINTIFY_API_BASE_URL).rstrip("/")
        self.shop_id = shop_id if shop_id is not None else settings.PRINTIFY_SHOP_ID
        self.timeout = getattr(settings, "PRINTIFY_REQUEST_TIMEOUT", 30)
        if not self.api_token:
            raise PrintifyNotConfiguredError(
                "PRINTIFY_API_TOKEN is not set. Add it to your .env to enable catalogue sync."
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "User-Agent": getattr(settings, "PRINTIFY_USER_AGENT", "Artverse/1.0"),
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
                response = self._session.request(method, url, timeout=self.timeout, **kwargs)
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


def fetch_shops(client: PrintifyClient | None = None) -> list[dict]:
    client = client or PrintifyClient()
    response = client.get("/shops.json")
    if not isinstance(response, list):
        raise PrintifyResponseError("Printify shops response must be a list.")
    return response


def validate_configured_shop() -> dict:
    """Confirm PRINTIFY_API_TOKEN + PRINTIFY_SHOP_ID are configured *and* that the configured
    shop is actually reachable with that token — a token string being present isn't enough to
    call the integration "connected." Returns {"id", "title", "sales_channel"} for the matched
    shop. Raises PrintifyNotConfiguredError / PrintifyAPIError / PrintifyResponseError /
    PrintifyShopNotFoundError — callers that just want a boolean can catch PrintifyError."""
    if not settings.PRINTIFY_API_TOKEN:
        raise PrintifyNotConfiguredError("PRINTIFY_API_TOKEN is not configured.")
    if not settings.PRINTIFY_SHOP_ID:
        raise PrintifyNotConfiguredError("PRINTIFY_SHOP_ID is not configured.")

    client = PrintifyClient()
    shops = fetch_shops(client)

    # Settings arrive as strings; Printify returns numeric IDs — normalize both sides before
    # comparing so "28270298" (from .env) matches 28270298 (from the API).
    configured_id = str(settings.PRINTIFY_SHOP_ID).strip()
    for shop in shops:
        shop_id = shop.get("id")
        if shop_id is not None and str(shop_id).strip() == configured_id:
            return {
                "id": shop_id,
                "title": shop.get("title", ""),
                "sales_channel": shop.get("sales_channel", ""),
            }

    raise PrintifyShopNotFoundError(
        f"Configured PRINTIFY_SHOP_ID ({configured_id}) was not found among the {len(shops)} "
        "shop(s) accessible with this API token. Check the token's permissions and the shop ID."
    )


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
    Existing rows keep their `mockup_template` mapping — only the synced fields are overwritten.

    Network calls (fetch_blueprints) happen before the transaction opens; all of the upserts
    happen inside one transaction.atomic() block so a mid-sync failure can't leave a half-synced
    catalogue, and so the fetch's (uncontrolled) latency doesn't hold DB locks.

    Known integration failures (PrintifyError) are recorded on the run and swallowed — existing
    callers (views, admin actions, the management command) rely on this and just check
    `run.status`. Genuinely unexpected exceptions are also recorded on the run, but re-raised —
    swallowing an unknown bug would leave both the run AND the underlying problem invisible."""
    run = PrintifySyncRun.objects.create(kind=PrintifySyncRun.Kind.BLUEPRINTS, triggered_by=triggered_by)
    try:
        client = PrintifyClient()
        blueprints = fetch_blueprints(client)

        created_count = updated_count = 0
        with transaction.atomic():
            for entry in blueprints:
                blueprint_id = entry.get("id")
                if blueprint_id is None:
                    continue
                _, created = PrintifyBlueprint.objects.update_or_create(
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
                created_count += int(created)
                updated_count += int(not created)
    except PrintifyError as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = safe_error_message(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        return run
    except Exception as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = safe_error_message(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        raise
    else:
        run.status = PrintifySyncRun.Status.SUCCESS
        run.blueprints_synced = created_count + updated_count
        run.blueprints_created = created_count
        run.blueprints_updated = updated_count
        run.finished_at = timezone.now()
        run.save()
        return run


def sync_print_providers_for_blueprint(blueprint: PrintifyBlueprint, provider_id: int | None = None, triggered_by=None) -> PrintifySyncRun:
    """Pull print providers + their variant/placeholder catalogue for one blueprint (or, with
    `provider_id`, just one specific provider) and upsert local PrintifyPrintProvider rows.

    Same fetch-then-persist split as sync_blueprints(): all Printify requests happen first, then
    all upserts happen inside one transaction.atomic() block, so a failure partway through
    (e.g. the 3rd of 22 providers) rolls back the whole blueprint's provider sync rather than
    leaving it half-updated, and the transaction never sits open across the network calls."""
    run = PrintifySyncRun.objects.create(
        kind=PrintifySyncRun.Kind.PROVIDERS, blueprint=blueprint, triggered_by=triggered_by
    )
    try:
        client = PrintifyClient()
        providers = fetch_print_providers(client, blueprint.blueprint_id)
        if provider_id is not None:
            providers = [p for p in providers if p.get("id") == provider_id]

        fetched = []
        for provider_entry in providers:
            pid = provider_entry.get("id")
            if pid is None:
                continue
            variant_data = fetch_print_provider_variants(client, blueprint.blueprint_id, pid)
            location = fetch_print_provider_location(client, pid)
            fetched.append((provider_entry, pid, variant_data, location))

        created_count = updated_count = 0
        variant_count = 0
        with transaction.atomic():
            for provider_entry, pid, variant_data, location in fetched:
                variants = variant_data.get("variants", []) if isinstance(variant_data, dict) else []
                _, created = PrintifyPrintProvider.objects.update_or_create(
                    blueprint=blueprint,
                    provider_id=pid,
                    defaults={
                        "title": provider_entry.get("title", ""),
                        "location": location,
                        "variants": variants,
                        "raw_data": {"provider": provider_entry, "variants_response": variant_data},
                    },
                )
                created_count += int(created)
                updated_count += int(not created)
                variant_count += len(variants)
    except PrintifyError as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = safe_error_message(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        return run
    except Exception as exc:
        run.status = PrintifySyncRun.Status.FAILED
        run.error_message = safe_error_message(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])
        raise
    else:
        run.status = PrintifySyncRun.Status.SUCCESS
        run.providers_synced = created_count + updated_count
        run.providers_created = created_count
        run.providers_updated = updated_count
        run.variants_synced = variant_count
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
    Returns a summary dict: {created, updated, marked_unavailable}.

    All writes happen inside one transaction.atomic() block — no network calls are made here
    (the provider's variant catalogue is already-synced local data), so this is pure DB work."""
    from apps.generator.models import ProductVariant

    from .validation import validate_provider_matches_template_blueprint

    template = product.mockup_template
    provider = template.selected_print_provider if template else None
    if provider is None:
        raise PrintifyError(
            "This product's mockup template has no mapped Printify print provider — "
            "map a blueprint and select a provider first."
        )

    # Re-verify the provider→blueprint→template chain at sync time, independent of whatever the
    # model already enforced at save time — a bulk update, fixture load, or a blueprint remap
    # after the provider was selected could have made this inconsistent since then.
    try:
        validate_provider_matches_template_blueprint(template.pk, provider)
    except ValueError as exc:
        raise PrintifyError(str(exc)) from exc

    provider_variants = provider.variants or []
    by_color_size: dict[tuple[str, str], dict] = {}
    for entry in provider_variants:
        options = entry.get("options") or {}
        key = (str(options.get("color", "")).strip(), str(options.get("size", "")).strip())
        by_color_size[key] = entry

    created = updated = marked_unavailable = 0

    with transaction.atomic():
        existing = {
            (variant.color_name.strip(), variant.size.strip()): variant
            for variant in ProductVariant.objects.filter(product=product)
        }

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
                    supported_print_areas=[
                        p.get("position") for p in entry.get("placeholders", []) if p.get("position")
                    ],
                )
                created += 1
            else:
                # Deliberately not touching base_cost/retail_price — see docstring.
                variant.external_provider = "Printify"
                variant.external_variant_id = external_variant_id
                variant.is_available = is_enabled
                variant.supported_print_areas = [
                    p.get("position") for p in entry.get("placeholders", []) if p.get("position")
                ]
                variant.save(
                    update_fields=["external_provider", "external_variant_id", "is_available", "supported_print_areas"]
                )
                updated += 1

        # Anything local that the provider no longer lists gets disabled, not deleted.
        for key, variant in existing.items():
            if key not in by_color_size and variant.is_available:
                variant.is_available = False
                variant.save(update_fields=["is_available"])
                marked_unavailable += 1

    return {"created": created, "updated": updated, "marked_unavailable": marked_unavailable}
