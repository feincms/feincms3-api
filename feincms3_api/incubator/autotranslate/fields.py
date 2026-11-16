"""
Gate 2: which strings are sent to the translator

The default goes by field type: editable ``CharField`` and ``TextField``
without ``choices``, minus slugs, emails, URLs and a few names. Three
adapters add what model fields don't show: django-prose-editor fields are
HTML, django-json-schema-editor data contributes its string properties, and
feincms3-forms fields keep their names, values and defaults.

Link fields (``URLField``, ``url``, ``*_url``, ``LINK_FIELDS``) aren't
translated but mapped, see ``feincms3_api.incubator.autotranslate.links``.

Kinds, JSON schema walking and the path notation are the package's own,
see ``feincms3_api.kinds`` and ``feincms3_api.paths``.
"""

import dataclasses
import re
from functools import cache

from django.apps import apps
from django.db import models

from feincms3_api.incubator.autotranslate.conf import config
from feincms3_api.incubator.autotranslate.datasets import is_translated_field, label
from feincms3_api.kinds import (
    CHOICES,
    HTML,
    LINK,
    MACHINE_NAMES as EXCLUDED_NAMES,
    TEXT,
    is_link_name as _is_link_name,
    json_string_kind,
    schema_paths as _schema_paths,
)


try:
    from django_prose_editor.fields import ProseEditorField
except ImportError:  # pragma: no cover
    ProseEditorField = None

try:
    from django_json_schema_editor.plugins import JSONPluginBase
except ImportError:  # pragma: no cover
    JSONPluginBase = None

try:
    from feincms3_forms.models import FormFieldBase
except ImportError:  # pragma: no cover
    FormFieldBase = None


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    path: str
    kind: str

    @property
    def field(self):
        return re.split(r"[.\[]", self.path, maxsplit=1)[0]

    def __str__(self):
        if self.kind == HTML:
            return f"{self.path} (html)"
        if self.kind == CHOICES:
            return f"{self.path} (labels)"
        return self.path


def json_proxies(model):
    """The django-json-schema-editor proxies of a concrete JSON plugin model"""
    if JSONPluginBase is None or not issubclass(model, JSONPluginBase):
        return []
    return list(getattr(model, "_proxy_types_map", {}).values())


def _schema_specs(schema, prefix, *, is_link):
    for path, sub in _schema_paths(schema, prefix):
        if (kind := json_string_kind(path, sub, is_link=is_link)) in {
            TEXT,
            HTML,
            LINK,
        }:
            yield FieldSpec(path, kind)


def _schema(model):
    return getattr(model, "SCHEMA", None)


def all_paths(model):
    """Every path below ``model``, for validating the settings"""
    paths = set()
    for field in model._meta.concrete_fields:
        paths.add(field.name)
        if field.name == "data" and (schema := _schema(model)):
            paths.update(path for path, _schema_ in _schema_paths(schema, "data"))
    return paths


def _entries(setting):
    return {entry.strip() for entry in config()[setting]}


def _is_listed(entries, model, path):
    return f"{label(model)}.{path}" in entries or (
        model._meta.proxy and f"{label(model._meta.concrete_model)}.{path}" in entries
    )


@cache
def field_specs(model):
    """
    The strings and links of ``model``; for a JSON plugin, ``model`` is one
    of its proxies
    """
    link_fields = _entries("LINK_FIELDS")
    exclude_fields = _entries("EXCLUDE_FIELDS")
    is_form_field = FormFieldBase is not None and issubclass(model, FormFieldBase)

    specs = []
    for field in model._meta.concrete_fields:
        name = field.name
        if (
            not field.editable
            or field.primary_key
            or field.is_relation
            or is_translated_field(field)
        ):
            continue
        if isinstance(field, models.URLField) or (
            isinstance(field, models.CharField) and _is_link_name(name)
        ):
            specs.append(FieldSpec(name, LINK))
            continue
        if _is_listed(link_fields, model, name):
            specs.append(FieldSpec(name, LINK))
            continue
        if name == "data" and (schema := _schema(model)):
            specs.extend(
                _schema_specs(
                    schema,
                    "data",
                    is_link=lambda path: _is_listed(link_fields, model, path),
                )
            )
            continue
        if (
            field.choices
            or name in EXCLUDED_NAMES
            or isinstance(field, (models.SlugField, models.EmailField))
            or not isinstance(field, (models.CharField, models.TextField))
        ):
            continue
        if is_form_field:
            # The name is the key submissions are stored under, and the
            # default value of a choice field names a value.
            if name in {"name", "default_value"}:
                continue
            if name == "choices":
                specs.append(FieldSpec(name, CHOICES))
                continue
        if ProseEditorField is not None and isinstance(field, ProseEditorField):
            specs.append(FieldSpec(name, HTML))
        else:
            specs.append(FieldSpec(name, TEXT))

    # Excluded links are neither translated nor mapped.
    return tuple(
        spec for spec in specs if not _is_listed(exclude_fields, model, spec.path)
    )


def specs_for_raw(raw):
    """The field specs of one serialized object of a dump"""
    model = apps.get_model(raw["model"])
    if proxies := json_proxies(model):
        type = raw["fields"].get("type")
        model = next((p for p in proxies if type == p.TYPE), model)
    return field_specs(model)


def listed_models(registry):
    """The models ``--list-fields`` prints, JSON plugins as their proxies"""
    result = []
    for model in registry.models():
        if proxies := json_proxies(model):
            result.extend(proxies)
        else:
            result.append(model)
    return sorted(set(result), key=label)


def list_fields(registry):
    lines = []
    for model in listed_models(registry):
        specs = field_specs(model)
        strings = [str(spec) for spec in specs if spec.kind != LINK]
        links = [spec.path for spec in specs if spec.kind == LINK]
        if not strings and not links:
            continue
        adapter = ""
        if json_proxies(model._meta.concrete_model):
            adapter = " (json-schema-editor)"
        elif FormFieldBase is not None and issubclass(model, FormFieldBase):
            adapter = " (feincms3-forms)"
        lines.append(f"{label(model)}{adapter}")
        if strings:
            lines.append(f"  {', '.join(strings)}")
        if links:
            lines.append(f"  links: {', '.join(links)}")
    return "\n".join(lines) + "\n"
