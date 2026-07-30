import os

from django.core.cache import cache
from storages.backends.s3 import S3Storage


class RailwayBucketMediaStorage(S3Storage):
    default_acl = None
    file_overwrite = False
    querystring_auth = True
    querystring_expire = int(os.getenv("RAILWAY_BUCKET_URL_EXPIRY", "3600"))
    location = os.getenv("RAILWAY_BUCKET_MEDIA_PREFIX", "media")

    # S3Storage.url() signs a fresh query string (new signature + timestamp) on every single
    # call — and the gallery/shop/generator serializers call `.url` on every image/thumbnail
    # field on every list request. That means the same file gets a different URL on every page
    # load, which defeats the browser's HTTP cache entirely: every visit re-downloads every
    # image from the bucket instead of ever getting a cache hit, however small the thumbnail.
    # Cache the signed URL per file name so repeat requests within the TTL reuse the exact same
    # URL string (and so the browser can actually cache the response) instead of re-signing.
    # TTL is kept well under `querystring_expire` so a cached URL is never served past the point
    # it would actually stop working. This cache is Django's default (per-process) unless a
    # shared backend (e.g. Redis) is configured — still a real improvement (repeat requests
    # hitting the same worker get a cache hit) even without one, just not perfectly shared
    # across multiple app workers/dynos.
    _URL_CACHE_TTL_SECONDS = max(60, int(os.getenv("RAILWAY_BUCKET_URL_EXPIRY", "3600")) - 300)

    def url(self, name, parameters=None, expire=None):
        if parameters:
            # A caller asking for non-default signing parameters wants a specific one-off URL —
            # don't cache/reuse those.
            return super().url(name, parameters=parameters, expire=expire)

        cache_key = f"railway-bucket-signed-url:{name}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return cached_url

        signed_url = super().url(name, parameters=parameters, expire=expire)
        cache.set(cache_key, signed_url, self._URL_CACHE_TTL_SECONDS)
        return signed_url
