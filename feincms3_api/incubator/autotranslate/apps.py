from django.apps import AppConfig, apps
from django.core import checks


class Feincms3AutotranslateConfig(AppConfig):
    name = "feincms3_api.incubator.autotranslate"
    label = "feincms3_autotranslate"
    verbose_name = "feincms3-autotranslate"

    def ready(self):
        checks.register(check_field_settings)


def _resolve(entry):
    """``app_label.model_name.path`` -> (model, path), or a problem"""
    app_label, _sep, rest = entry.partition(".")
    model_name, _sep, path = rest.partition(".")
    try:
        model = apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return None, f"{entry!r} names no model."
    if not path:
        return None, f"{entry!r} names no field."
    return model, path


def check_field_settings(app_configs=None, **kwargs):
    from feincms3_api.incubator.autotranslate.conf import config  # noqa: PLC0415
    from feincms3_api.incubator.autotranslate.fields import all_paths  # noqa: PLC0415

    errors = []
    for setting in ("EXCLUDE_FIELDS", "LINK_FIELDS"):
        for entry in config()[setting]:
            model, path = _resolve(entry)
            if model is None:
                problem = path
            elif path not in all_paths(model):
                problem = f"{entry!r}: {model._meta.label_lower} has no {path!r}."
            else:
                continue
            errors.append(
                checks.Error(
                    f'FEINCMS3_AUTOTRANSLATE["{setting}"]: {problem}',
                    id="feincms3_autotranslate.E001",
                )
            )
    return errors
