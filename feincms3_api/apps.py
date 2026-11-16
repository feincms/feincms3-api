import contextlib

from django.apps import AppConfig, apps
from django.core import checks


class Feincms3APIConfig(AppConfig):
    name = "feincms3_api"
    verbose_name = "feincms3-api"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        checks.register(check_resources)
        checks.register(check_exclude_fields)


def _content_apis():
    # Content APIs are usually created where the URLs are, so load them.
    from django.urls import get_resolver  # noqa: PLC0415

    from feincms3_api.api import _instances  # noqa: PLC0415

    # Django's own URL checks report an URLconf which doesn't load.
    with contextlib.suppress(Exception):
        get_resolver().url_patterns  # noqa: B018
    return list(_instances)


def check_resources(app_configs=None, **kwargs):
    errors = []
    for api in _content_apis():
        for resource in api.resources:
            if resource.model not in resource.admin_site._registry:
                errors.append(
                    checks.Error(
                        f"{resource.type} is registered with a content API but"
                        f" not with the admin site {resource.admin_site.name!r}.",
                        hint="The API takes its forms and permissions from the admin.",
                        id="feincms3_api.E001",
                    )
                )
    return errors


def check_exclude_fields(app_configs=None, **kwargs):
    from feincms3_api.conf import config  # noqa: PLC0415
    from feincms3_api.paths import tokens  # noqa: PLC0415

    errors = []
    for entry in config()["EXCLUDE_FIELDS"]:
        app_label, _sep, rest = entry.strip().partition(".")
        model_name, _sep, path = rest.partition(".")
        try:
            model = apps.get_model(app_label, model_name)
        except (LookupError, ValueError):
            model = None
        names = {field.name for field in model._meta.get_fields()} if model else set()
        first = tokens(path)[0] if path else None
        if first is None or first not in names:
            errors.append(
                checks.Error(
                    f"FEINCMS3_API['EXCLUDE_FIELDS'] entry {entry!r} names no"
                    " model and field.",
                    id="feincms3_api.E002",
                )
            )
    return errors
