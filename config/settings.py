from pathlib import Path
import os
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    import sys
    sys.stderr.write(
        "\n[WARNING] 'python-dotenv' is not installed in this Python environment.\n"
        "Your '.env' file will NOT be loaded automatically.\n"
        "To fix this, run: pip install python-dotenv\n\n"
    )


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "artverse-dev-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
MAINTENANCE_TOKEN_MAX_AGE = int(os.getenv("DJANGO_MAINTENANCE_TOKEN_MAX_AGE", "86400"))

# Printify catalogue integration (Priority 3). Token/shop ID are secrets — never expose them to
# the frontend, never surface them in Django admin; all Printify requests go through
# apps.printify.services on the backend only.
PRINTIFY_API_TOKEN = os.getenv("PRINTIFY_API_TOKEN", "").strip()
PRINTIFY_SHOP_ID = os.getenv("PRINTIFY_SHOP_ID", "").strip()
PRINTIFY_API_BASE_URL = os.getenv("PRINTIFY_API_BASE_URL", "https://api.printify.com/v1").rstrip("/")
PRINTIFY_USER_AGENT = os.getenv("PRINTIFY_USER_AGENT", "Artverse/1.0")
PRINTIFY_REQUEST_TIMEOUT = int(os.getenv("PRINTIFY_REQUEST_TIMEOUT", "30"))
# Explicit kill switch, independent of whether a token happens to be set — lets an operator
# disable the integration (e.g. during an incident) without removing credentials from .env.
PRINTIFY_ENABLED = env_bool("PRINTIFY_ENABLED", False)

# AI image generation (apps.generator's Gemini-backed GenerationRequest/GeneratedImage flow).
# Server-side only — never exposed to the frontend. Same kill-switch pattern as Printify above.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash-image")
GEMINI_ENABLED = env_bool("GEMINI_ENABLED", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    "http://127.0.0.1:3000,http://localhost:3000",
)

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000",
)

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
    "apps.cart",
    "apps.printify",
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
        "DIRS": [BASE_DIR / "templates"],
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

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

RAILWAY_BUCKET_NAME = (os.getenv("BUCKET") or os.getenv("AWS_STORAGE_BUCKET_NAME", "")).strip()
RAILWAY_BUCKET_ENDPOINT = (os.getenv("ENDPOINT") or os.getenv("AWS_S3_ENDPOINT_URL", "")).strip()
RAILWAY_BUCKET_ACCESS_KEY = (os.getenv("ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID", "")).strip()
RAILWAY_BUCKET_SECRET_KEY = (os.getenv("SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")).strip()
RAILWAY_BUCKET_REGION = (os.getenv("REGION") or os.getenv("AWS_S3_REGION_NAME", "auto")).strip()
RAILWAY_BUCKET_ADDRESSING_STYLE = os.getenv("RAILWAY_BUCKET_ADDRESSING_STYLE", "virtual")
FORCE_RAILWAY_BUCKET = env_bool("USE_RAILWAY_BUCKET", False)

USE_RAILWAY_BUCKET = (
    FORCE_RAILWAY_BUCKET or
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
    missing_bucket_settings = [
        name
        for name, value in {
            "BUCKET": RAILWAY_BUCKET_NAME,
            "ENDPOINT": RAILWAY_BUCKET_ENDPOINT,
            "ACCESS_KEY_ID": RAILWAY_BUCKET_ACCESS_KEY,
            "SECRET_ACCESS_KEY": RAILWAY_BUCKET_SECRET_KEY,
        }.items()
        if not value
    ]

    if missing_bucket_settings:
        raise ImproperlyConfigured(
            "Railway bucket storage is enabled but required bucket variables are missing: "
            f"{', '.join(missing_bucket_settings)}. "
            "On Railway, attach your Bucket credentials to this service using Variable References. "
            "For Railway Buckets the ENDPOINT should typically look like https://storage.railway.app."
        )

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
