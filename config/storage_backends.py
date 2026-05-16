import os

from storages.backends.s3 import S3Storage


class RailwayBucketMediaStorage(S3Storage):
    default_acl = None
    file_overwrite = False
    querystring_auth = True
    querystring_expire = int(os.getenv("RAILWAY_BUCKET_URL_EXPIRY", "3600"))
    location = os.getenv("RAILWAY_BUCKET_MEDIA_PREFIX", "media")
