"""
Codecs: one form field in wire format, and its JSON schema

Codecs are looked up by form field class along its MRO, so a project's
subclass of a known form field uses that field's codec without being
registered. A form field without a codec isn't guessed at: the field is left
out, and ``/schema/`` and the snapshot list it as excluded.
"""

import copy
import dataclasses
import datetime as dt
import decimal

from django import forms
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from feincms3_api.kinds import (
    CHOICES,
    HTML,
    LINK,
    MACHINE,
    MACHINE_NAMES,
    TEXT,
    is_link_name,
    json_string_kind,
    schema_paths,
)
from feincms3_api.paths import tokens


try:
    from django_prose_editor.fields import ProseEditorFormField
except ImportError:  # pragma: no cover
    ProseEditorFormField = None

try:
    from django_json_schema_editor.forms import JSONEditorField
except ImportError:  # pragma: no cover
    JSONEditorField = None

try:
    from feincms3_forms.models import FormFieldBase
except ImportError:  # pragma: no cover
    FormFieldBase = None


KIND = "x-feincms3-kind"
REF = "x-feincms3-ref"


def label(model):
    return model._meta.label_lower


@dataclasses.dataclass
class Field:
    """One field of a form, as the API exposes it"""

    name: str
    form_field: forms.Field
    model: type[models.Model]
    read_only: bool = False

    @property
    def model_field(self):
        try:
            return self.model._meta.get_field(self.name)
        except FieldDoesNotExist:
            return None


class Codec:
    """Values pass through as they are; subclasses describe their type"""

    def to_wire(self, value, field):
        return value

    def json_schema(self, field):
        return {}


def _nullable(schema, field):
    if field.form_field.required:
        return schema
    model_field = field.model_field
    if model_field is not None and getattr(model_field, "null", False):
        schema["type"] = [schema["type"], "null"]
    return schema


def string_kind(field):
    """The kind of a plain string form field"""
    form_field, name = field.form_field, field.name
    if isinstance(form_field, forms.URLField) or is_link_name(name):
        return LINK
    if name in MACHINE_NAMES or isinstance(
        form_field,
        (
            forms.SlugField,
            forms.EmailField,
            forms.RegexField,
            forms.GenericIPAddressField,
            forms.UUIDField,
        ),
    ):
        return MACHINE
    if FormFieldBase is not None and issubclass(field.model, FormFieldBase):
        # The name is the key submissions are stored under, and the default
        # value of a choice field names a value.
        if name in {"name", "default_value"}:
            return MACHINE
        if name == "choices":
            return CHOICES
    return TEXT


class CharCodec(Codec):
    kind = None

    def to_wire(self, value, field):
        return value

    def json_schema(self, field):
        form_field = field.form_field
        schema = {"type": "string", KIND: self.kind or string_kind(field)}
        if form_field.max_length is not None:
            schema["maxLength"] = form_field.max_length
        if form_field.min_length is not None:
            schema["minLength"] = form_field.min_length
        if isinstance(form_field, forms.URLField):
            schema["format"] = "uri"
        return _nullable(schema, field)


class HTMLCodec(CharCodec):
    kind = HTML

    def json_schema(self, field):
        return {**super().json_schema(field), "contentMediaType": "text/html"}


class BooleanCodec(Codec):
    def json_schema(self, field):
        if isinstance(field.form_field, forms.NullBooleanField):
            return {"type": ["boolean", "null"]}
        return {"type": "boolean"}


class IntegerCodec(Codec):
    def json_schema(self, field):
        schema = {"type": "integer"}
        if (minimum := field.form_field.min_value) is not None:
            schema["minimum"] = minimum
        if (maximum := field.form_field.max_value) is not None:
            schema["maximum"] = maximum
        return _nullable(schema, field)


class FloatCodec(IntegerCodec):
    def json_schema(self, field):
        return {**super().json_schema(field), "type": "number"}


class DecimalCodec(Codec):
    """Decimals as strings, so no precision is lost on the way"""

    def to_wire(self, value, field):
        return None if value is None else str(value)

    def json_schema(self, field):
        return _nullable({"type": "string", "format": "decimal"}, field)


class TemporalCodec(Codec):
    def __init__(self, format):
        self.format = format

    def to_wire(self, value, field):
        if isinstance(value, (dt.date, dt.time)):
            return value.isoformat()
        if isinstance(value, dt.timedelta):
            return str(value)
        return value

    def json_schema(self, field):
        return _nullable({"type": "string", "format": self.format}, field)


def _choice_values(choices):
    values = []
    for key, value in choices:
        if isinstance(value, (list, tuple)):
            values.extend(_choice_values(value))
        else:
            values.append(key.value if hasattr(key, "value") else key)
    return values


def _json_value(value):
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value


class ChoiceCodec(Codec):
    def json_schema(self, field):
        values = [_json_value(v) for v in _choice_values(field.form_field.choices)]
        schema = {"enum": values}
        if all(isinstance(v, str) for v in values):
            schema = {"type": "string", **schema, KIND: MACHINE}
        return schema


class MultipleChoiceCodec(Codec):
    def to_wire(self, value, field):
        return list(value or ())

    def json_schema(self, field):
        values = [_json_value(v) for v in _choice_values(field.form_field.choices)]
        return {"type": "array", "items": {"enum": values}}


def reference(obj_or_pk, model):
    if obj_or_pk is None or obj_or_pk == "":
        return None
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return {"type": label(model), "id": pk}


def reference_schema(model):
    return {
        "type": "object",
        "properties": {
            "type": {"const": label(model)},
            "id": {},
        },
        "required": ["type", "id"],
        REF: label(model),
    }


class ModelChoiceCodec(Codec):
    def to_wire(self, value, field):
        return reference(value, field.form_field.queryset.model)

    def json_schema(self, field):
        schema = reference_schema(field.form_field.queryset.model)
        if not field.form_field.required:
            schema["type"] = ["object", "null"]
        return schema


class ModelMultipleChoiceCodec(Codec):
    def to_wire(self, value, field):
        model = field.form_field.queryset.model
        return [reference(item, model) for item in value or ()]

    def json_schema(self, field):
        return {
            "type": "array",
            "items": reference_schema(field.form_field.queryset.model),
        }


class FileCodec(Codec):
    """
    A file of the object itself (not a django-cabinet reference): its name
    and URL. Written through ``/uploads/``, see the spec.
    """

    def to_wire(self, value, field):
        if not value:
            return None
        try:
            url = value.url
        except ValueError:
            url = None
        return {"name": value.name, "url": url}

    def json_schema(self, field):
        return {
            "type": ["object", "null"],
            "properties": {
                "name": {"type": "string", KIND: MACHINE},
                "url": {"type": ["string", "null"], KIND: MACHINE},
            },
            "x-feincms3-file": True,
        }


class JSONCodec(Codec):
    """JSON without a schema: passed through, with an empty schema"""


def annotate_json_schema(schema, prefix):
    """A copy of ``schema`` with the kind of every string in it"""
    schema = copy.deepcopy(schema)
    for path, sub in schema_paths(schema, prefix):
        if (kind := json_string_kind(path, sub)) is not None:
            sub[KIND] = kind
    return schema


class JSONEditorCodec(Codec):
    """django-json-schema-editor data: the editor's own schema"""

    def json_schema(self, field):
        return annotate_json_schema(field.form_field._schema or {}, field.name)


class CodecRegistry:
    def __init__(self):
        self._codecs = {}

    def register(self, form_field_class, codec):
        self._codecs[form_field_class] = codec

    def copy(self):
        registry = CodecRegistry()
        registry._codecs = dict(self._codecs)
        return registry

    def lookup(self, form_field):
        for cls in type(form_field).__mro__:
            if (codec := self._codecs.get(cls)) is not None:
                return codec
        return None


codecs = CodecRegistry()
codecs.register(forms.CharField, CharCodec())
codecs.register(forms.BooleanField, BooleanCodec())
codecs.register(forms.IntegerField, IntegerCodec())
codecs.register(forms.FloatField, FloatCodec())
codecs.register(forms.DecimalField, DecimalCodec())
codecs.register(forms.DateField, TemporalCodec("date"))
codecs.register(forms.DateTimeField, TemporalCodec("date-time"))
codecs.register(forms.TimeField, TemporalCodec("time"))
codecs.register(forms.DurationField, TemporalCodec("duration"))
codecs.register(forms.ChoiceField, ChoiceCodec())
codecs.register(forms.MultipleChoiceField, MultipleChoiceCodec())
codecs.register(forms.ModelChoiceField, ModelChoiceCodec())
codecs.register(forms.ModelMultipleChoiceField, ModelMultipleChoiceCodec())
codecs.register(forms.JSONField, JSONCodec())
codecs.register(forms.FileField, FileCodec())
if ProseEditorFormField is not None:
    codecs.register(ProseEditorFormField, HTMLCodec())
if JSONEditorField is not None:
    codecs.register(JSONEditorField, JSONEditorCodec())


def describe(name, schema):
    """
    Lines for the snapshot: ``name: kind`` for this field and, for JSON data,
    for every value inside it
    """
    suffix = " (read-only)" if schema.get("readOnly") else ""
    if _is_leaf(schema):
        return [f"{name}: {_describe_one(schema)}{suffix}"]
    lines = [f"{name}: json{suffix}"]
    lines.extend(
        f"{path}: {_describe_one(sub)}"
        for path, sub in _leaf_paths(schema, name)
        if path != name
    )
    return lines


def _is_leaf(schema):
    return (
        REF in schema
        or schema.get("x-feincms3-file")
        or schema.get("type") not in {"object", "array"}
        or (schema.get("type") == "array" and REF in schema.get("items", {}))
    )


def _leaf_paths(schema, prefix):
    if _is_leaf(schema):
        yield prefix, schema
    elif schema.get("type") == "object":
        for key, sub in schema.get("properties", {}).items():
            yield from _leaf_paths(sub, f"{prefix}.{key}")
    elif isinstance(schema.get("items"), dict):
        yield from _leaf_paths(schema["items"], f"{prefix}[]")


def _describe_one(schema):
    if schema.get("x-feincms3-file"):
        return "file"
    if REF in schema:
        return f"ref {schema[REF]}"
    if schema.get("type") == "array" and REF in schema.get("items", {}):
        return f"refs {schema['items'][REF]}"
    if KIND in schema:
        kind = schema[KIND]
        return "choice" if "enum" in schema and kind == MACHINE else kind
    if "enum" in schema:
        return "choice"
    type = schema.get("type")
    if isinstance(type, list):
        type = next((t for t in type if t != "null"), "null")
    return type or "json"


__all__ = [
    "Codec",
    "CodecRegistry",
    "Field",
    "codecs",
    "describe",
    "label",
    "reference",
    "tokens",
]
