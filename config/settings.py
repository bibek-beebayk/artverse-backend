from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "artverse-dev-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DJANGO_CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    ).split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "storages",
    "rest_framework",
    "rest_framework_simplejwt",
    "apps.accounts",
    "apps.gallery",
    "apps.shop",
    "apps.generator",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

POSTGRES_DB = os.getenv("PGDATABASE")

if POSTGRES_DB:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": POSTGRES_DB,
            "USER": os.getenv("PGUSER", "postgres"),
            "PASSWORD": os.getenv("PGPASSWORD", "password"),
            "HOST": os.getenv("PGHOST", "localhost"),
            "PORT": os.getenv("PGPORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

RAILWAY_BUCKET_NAME = os.getenv("BUCKET") or os.getenv("AWS_STORAGE_BUCKET_NAME", "")
RAILWAY_BUCKET_ENDPOINT = os.getenv("ENDPOINT") or os.getenv("AWS_S3_ENDPOINT_URL", "")
RAILWAY_BUCKET_ACCESS_KEY = os.getenv("ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID", "")
RAILWAY_BUCKET_SECRET_KEY = os.getenv("SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")
RAILWAY_BUCKET_REGION = os.getenv("REGION") or os.getenv("AWS_S3_REGION_NAME", "auto")
RAILWAY_BUCKET_ADDRESSING_STYLE = os.getenv("RAILWAY_BUCKET_ADDRESSING_STYLE", "virtual")

USE_RAILWAY_BUCKET = (
    env_bool("USE_RAILWAY_BUCKET", False) or
    all(
        [
            RAILWAY_BUCKET_NAME,
            RAILWAY_BUCKET_ENDPOINT,
            RAILWAY_BUCKET_ACCESS_KEY,
            RAILWAY_BUCKET_SECRET_KEY,
        ]
    )
)

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if USE_RAILWAY_BUCKET:
    AWS_S3_ENDPOINT_URL = RAILWAY_BUCKET_ENDPOINT
    AWS_STORAGE_BUCKET_NAME = RAILWAY_BUCKET_NAME
    AWS_ACCESS_KEY_ID = RAILWAY_BUCKET_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY = RAILWAY_BUCKET_SECRET_KEY
    AWS_S3_REGION_NAME = RAILWAY_BUCKET_REGION
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = RAILWAY_BUCKET_ADDRESSING_STYLE
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("RAILWAY_BUCKET_URL_EXPIRY", "3600"))
    AWS_S3_FILE_OVERWRITE = False

    # Railway Buckets are private, so Django serves signed URLs for media.
    STORAGES["default"] = {
        "BACKEND": "config.storage_backends.RailwayBucketMediaStorage",
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny",
    ),
}
