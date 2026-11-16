from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

DEBUG = True
SECRET_KEY = "unsafe"
ALLOWED_HOSTS = ["example.com", ".example.com"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "feincms3",
    "content_editor",
    "tree_queries",
    "django_prose_editor",
    "django_json_schema_editor",
    "feincms3_forms",
    "feincms3_data",
    "feincms3_api",
    "feincms3_api.incubator.autotranslate",
    "testapp",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
            ]
        },
    }
]
ROOT_URLCONF = "testapp.urls"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
STATIC_URL = "/static/"

LANGUAGE_CODE = "de"
LANGUAGES = [("de", "German"), ("fr", "French"), ("it", "Italian")]

FEINCMS3_DATA_DATASETS = "testapp.datasets.datasets"
FEINCMS3_AUTOTRANSLATE = {
    "GLOSSARY": BASE_DIR / "glossary.toml",
    "INSTRUCTIONS": "Formal register.",
    "EXCLUDE_FIELDS": [
        "testapp.form.recipient_email",
        "testapp.jsonplugin_key_figures.data.figures[].unit",
    ],
}
FEINCMS3_API = {
    "EXCLUDE_FIELDS": [
        "testapp.training.slug",
        "testapp.jsonplugin_key_figures.data.figures[].unit",
    ],
}

SILENCED_SYSTEM_CHECKS = ["django_prose_editor.W001", "django_prose_editor.W004"]
