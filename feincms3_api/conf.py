import datetime as dt

from django.conf import settings


DEFAULTS = {
    # ``app_label.model_name.field``, or a path into JSON data
    # (``app_label.model_name.data.figures[].unit``): never exposed.
    "EXCLUDE_FIELDS": [],
    "MAX_PAGE_SIZE": 200,
    "DEFAULT_PAGE_SIZE": 50,
    # Operations in one ``POST /batch/``
    "MAX_BATCH_OPERATIONS": 100,
    # About one editing session; a new token for the next one
    "TOKEN_LIFETIME": dt.timedelta(hours=8),
    # Staged uploads: unused ones are removed by ``f3api_cleanup``
    "UPLOAD_LIFETIME": dt.timedelta(days=1),
    "UPLOAD_STORAGE": "default",
}


def config():
    return DEFAULTS | getattr(settings, "FEINCMS3_API", {})


def excluded_paths(type_label):
    """The excluded paths of one type, without the type: ``["data.unit"]``"""
    prefix = f"{type_label}."
    return [
        entry.strip().removeprefix(prefix)
        for entry in config()["EXCLUDE_FIELDS"]
        if entry.strip().startswith(prefix)
    ]
