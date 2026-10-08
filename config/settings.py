import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent

# This local-only app has no sessions or persisted authentication state, so an
# ephemeral key avoids storing a credential in source or requiring one at setup.
SECRET_KEY = secrets.token_urlsafe(50)
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "observability",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Indiana/Indianapolis"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, "https://cdn.jsdelivr.net"],
}

ORCHESTRATOR_ROOT = Path(
    os.environ.get("REALMS_ORCHESTRATOR_ROOT", BASE_DIR.parent / "orchestrator_code")
).resolve()
ORCHESTRATOR_MCP_TIMEOUT_SECONDS = float(os.environ.get("REALMS_MCP_TIMEOUT_SECONDS", "15"))
ORCHESTRATOR_OBSERVED_TIMEOUT_SECONDS = float(
    os.environ.get("REALMS_OBSERVED_MCP_TIMEOUT_SECONDS", "305")
)

# Browser-safe model labels are configured independently from the orchestrator.
# They are validated again before entering an API response and never sourced
# from the orchestrator's environment or dotenv file.
OBSERVABILITY_MODEL_CATALOG = {
    "router": os.environ.get("REALMS_OBSERVABILITY_ROUTER_MODEL"),
    "reviewer": os.environ.get("REALMS_OBSERVABILITY_REVIEWER_MODEL"),
    "judge": os.environ.get("REALMS_OBSERVABILITY_JUDGE_MODEL"),
}
OBSERVABILITY_EXECUTOR_LABEL = os.environ.get("REALMS_OBSERVABILITY_EXECUTOR")
OBSERVABILITY_MOCK_MODE = os.environ.get("REALMS_OBSERVABILITY_MOCK", "false").lower() == "true"
OBSERVABILITY_STORAGE_BACKEND = os.environ.get(
    "REALMS_OBSERVABILITY_STORAGE", "memory"
).lower()
OBSERVABILITY_SQLITE_PATH = os.environ.get("REALMS_OBSERVABILITY_SQLITE_PATH")


def _bounded_env_int(name: str, default: int, *, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be an integer.") from error
    if not 1 <= value <= maximum:
        raise ImproperlyConfigured(f"{name} must be between 1 and {maximum}.")
    return value


OBSERVABILITY_SQLITE_MAX_RUNS = _bounded_env_int(
    "REALMS_OBSERVABILITY_SQLITE_MAX_RUNS", 500, maximum=100_000
)
OBSERVABILITY_SQLITE_RETENTION_DAYS = _bounded_env_int(
    "REALMS_OBSERVABILITY_SQLITE_RETENTION_DAYS", 7, maximum=3_650
)
if OBSERVABILITY_STORAGE_BACKEND not in {"memory", "sqlite"}:
    raise ImproperlyConfigured("REALMS_OBSERVABILITY_STORAGE must be memory or sqlite.")
if OBSERVABILITY_STORAGE_BACKEND == "sqlite" and not OBSERVABILITY_SQLITE_PATH:
    raise ImproperlyConfigured(
        "REALMS_OBSERVABILITY_SQLITE_PATH is required when SQLite history is enabled."
    )
