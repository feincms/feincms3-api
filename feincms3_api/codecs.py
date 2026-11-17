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

from feincms3_api.errors import WireError
from feincms3_api.kinds import (
    CHOICES,
    HTML,
    LINK,
    MACHINE,
    TEXT,
    is_link_name,
    is_machine_name,
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
HTML_ALLOWED = "x-feincms3-html"


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

    def from_wire(self, value, field):
        """
        The value the form field's ``clean()`` gets; raise ``WireError`` if
        the value can't be one
        """
        return value

    def json_schema(self, field):
        return {}

    def references(self, value, field):
        """``(type, id)`` of the objects a wire value refers to"""
        return []


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
    if is_machine_name(name) or isinstance(
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

    def from_wire(self, value, field):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise WireError("Expected a string.")
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
        schema = {**super().json_schema(field), "contentMediaType": "text/html"}
        if (allowed := allowed_html(field)) is not None:
            schema[HTML_ALLOWED] = allowed
        return schema


def allowed_html(field):
    """
    The markup a django-prose-editor field allows, from its extensions, as
    nh3's allowlist (``tags``, ``attributes``, ``url_schemes``, …), and
    whether the form or model field removes everything else
    (``sanitize=True``); ``None`` for fields without extensions
    (prose-editor's legacy mode)
    """
    form_field = field.form_field
    extensions = (getattr(form_field, "config", None) or {}).get("extensions")
    if extensions is None:
        return None
    from django_prose_editor import fields  # noqa: PLC0415
    from django_prose_editor.config import allowlist_from_extensions  # noqa: PLC0415

    allowed = {}
    for key, value in allowlist_from_extensions(extensions).items():
        if isinstance(value, set | frozenset | list | tuple):
            allowed[key] = sorted(value)
        elif isinstance(value, dict):
            allowed[key] = {tag: sorted(items) for tag, items in sorted(value.items())}
    # Without sanitising, prose-editor only drops empty paragraphs.
    passing = {
        getattr(fields, "_identity", None),
        getattr(fields, "_actually_empty", None),
    }
    allowed["sanitized"] = any(
        getattr(f, "sanitize", None) not in passing
        for f in (form_field, field.model_field)
        if hasattr(f, "sanitize")
    )
    return allowed


class BooleanCodec(Codec):
    def from_wire(self, value, field):
        if value is None and isinstance(field.form_field, forms.NullBooleanField):
            return None
        if not isinstance(value, bool):
            raise WireError("Expected true or false.")
        return value

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

    def from_wire(self, value, field):
        if isinstance(value, float):
            raise WireError("Expected a string, not a float, to keep the precision.")
        return value

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


class SplitDateTimeCodec(TemporalCodec):
    """The admin's date and time inputs: an ISO date-time on the wire"""

    def __init__(self):
        super().__init__("date-time")

    def from_wire(self, value, field):
        if value in {None, ""}:
            return [None, None]
        try:
            parsed = dt.datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise WireError("Expected an ISO 8601 date and time.") from exc
        return [parsed.date().isoformat(), parsed.time().isoformat()]


class MultipleChoiceCodec(Codec):
    def to_wire(self, value, field):
        return list(value or ())

    def from_wire(self, value, field):
        if not isinstance(value, list):
            raise WireError("Expected a list.")
        return value

    def json_schema(self, field):
        values = [_json_value(v) for v in _choice_values(field.form_field.choices)]
        return {"type": "array", "items": {"enum": values}}


def reference(obj_or_pk, model):
    if obj_or_pk is None or obj_or_pk == "":
        return None
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return {"type": label(model), "id": pk}


def reference_pk(value, model):
    """The primary key of a reference: ``{"type", "id"}`` or the bare id"""
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get("type") != label(model):
            raise WireError(f"Expected a reference to {label(model)}.")
        if "id" not in value:
            raise WireError("A reference needs an id.")
        return value["id"]
    if isinstance(value, (int, str)):
        return value
    raise WireError(f"Expected a reference to {label(model)}.")


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

    def references(self, value, field):
        return [(value["type"], value["id"])] if isinstance(value, dict) else []

    def from_wire(self, value, field):
        return reference_pk(value, field.form_field.queryset.model)

    def json_schema(self, field):
        schema = reference_schema(field.form_field.queryset.model)
        if not field.form_field.required:
            schema["type"] = ["object", "null"]
        return schema


class ModelMultipleChoiceCodec(Codec):
    def to_wire(self, value, field):
        model = field.form_field.queryset.model
        return [reference(item, model) for item in value or ()]

    def references(self, value, field):
        return [
            (item["type"], item["id"]) for item in value or () if isinstance(item, dict)
        ]

    def from_wire(self, value, field):
        if not isinstance(value, list):
            raise WireError("Expected a list of references.")
        model = field.form_field.queryset.model
        return [reference_pk(item, model) for item in value]

    def json_schema(self, field):
        return {
            "type": "array",
            "items": reference_schema(field.form_field.queryset.model),
        }


@dataclasses.dataclass(frozen=True)
class UploadRef:
    """``{"upload": id}``, resolved against the user's uploads when binding"""

    id: str


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

    def from_wire(self, value, field):
        """
        ``None`` clears the file; what was read keeps it; ``{"upload": id}``
        is a file from ``/uploads/``
        """
        if value is None:
            if field.form_field.required:
                # Django's FileField would keep the file without a word.
                raise WireError("Required, it can't be cleared.")
            return False  # Like the admin's "clear" checkbox
        if isinstance(value, dict) and "upload" in value:
            return UploadRef(str(value["upload"]))
        if isinstance(value, dict) and "name" in value:
            return None  # Unchanged
        raise WireError('Expected null, the file as read, or {"upload": id}.')

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
    """
    django-json-schema-editor data: the editor's own schema

    References inside the data (``format: foreign_key``) stay bare ids, as
    stored; the editor's ``foreign_key_descriptions`` say what they refer
    to, and where if they're given as paths (``foreign_key_paths``).
    """

    def json_schema(self, field):
        schema = annotate_json_schema(field.form_field._schema or {}, field.name)
        by_path = dict(schema_paths(schema, field.name))
        for type_label, getter in _foreign_key_descriptions(field):
            for jmespath in getattr(getter, "keywords", {}).get("paths", ()):
                # The simple ones are paths in our notation too
                path = f"{field.name}.{jmespath.replace('[*]', '[]')}"
                if (sub := by_path.get(path)) is not None:
                    sub[REF] = type_label
        return schema

    def references(self, value, field):
        refs = []
        for type_label, getter in _foreign_key_descriptions(field):
            try:
                pks = getter(value or {})
            except (TypeError, ValueError):
                continue
            refs.extend((type_label, pk) for pk in pks)
        return refs


def _foreign_key_descriptions(field):
    return getattr(field.form_field, "_foreign_key_descriptions", None) or ()


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
codecs.register(forms.SplitDateTimeField, SplitDateTimeCodec())
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


__all__ = [
    "Codec",
    "CodecRegistry",
    "Field",
    "codecs",
    "label",
    "reference",
    "tokens",
]
