import contextlib

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured


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
    from feincms3_api.codecs import label  # noqa: PLC0415

    errors = []
    for api in _content_apis():
        for resource in api.resources:
            try:
                model_admin = resource.model_admin
            except ImproperlyConfigured:
                errors.append(
                    checks.Error(
                        f"{resource.type} is registered with a content API but"
                        f" not with the admin site {resource.admin_site.name!r}.",
                        hint="The API takes its forms and permissions from the admin.",
                        id="feincms3_api.E001",
                    )
                )
                continue
            # Plugins and other inlines are keyed by their model's type.
            seen = set()
            for inline in model_admin.inlines:
                if (type_label := label(inline.model)) in seen:
                    errors.append(
                        checks.Error(
                            f"The admin of {resource.type} has more than one"
                            f" inline for {type_label}.",
                            hint="The API keys inlines by their model's type.",
                            id="feincms3_api.E003",
                        )
                    )
                seen.add(type_label)
    return errors


def _names_field(entry):
    """Whether ``entry`` (``app_label.model_name.field…``) names a field"""
    from feincms3_api.paths import tokens  # noqa: PLC0415

    app_label, _sep, rest = entry.strip().partition(".")
    model_name, _sep, path = rest.partition(".")
    try:
        model = apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return False
    names = {field.name for field in model._meta.get_fields()}
    return bool(path) and tokens(path)[0] in names


def check_exclude_fields(app_configs=None, **kwargs):
    from feincms3_api.conf import config  # noqa: PLC0415
    from feincms3_api.kinds import CHOICES, HTML, LINK, MACHINE, TEXT  # noqa: PLC0415

    errors = [
        checks.Error(
            f"FEINCMS3_API['EXCLUDE_FIELDS'] entry {entry!r} names no model and field.",
            id="feincms3_api.E002",
        )
        for entry in config()["EXCLUDE_FIELDS"]
        if not _names_field(entry)
    ]
    kinds = {TEXT, HTML, LINK, MACHINE, CHOICES}
    for entry, kind in config()["KINDS"].items():
        if not _names_field(entry):
            errors.append(
                checks.Error(
                    f"FEINCMS3_API['KINDS'] entry {entry!r} names no model and field.",
                    id="feincms3_api.E004",
                )
            )
        if kind not in kinds:
            errors.append(
                checks.Error(
                    f"FEINCMS3_API['KINDS'] entry {entry!r}: {kind!r} isn't one"
                    f" of {', '.join(sorted(kinds))}.",
                    id="feincms3_api.E004",
                )
            )
    return errors
