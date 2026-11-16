"""
Adapters decide what an object's or a plugin's ``fields`` are

The default adapter is the admin's form: its fields, through the codecs.
That covers every plugin made of ordinary fields without any code. Projects
register their own adapter for a type only to change the shape.
"""

import copy
import dataclasses
import re

from django import forms
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models, transaction

from feincms3_api.codecs import Field, UploadRef, codecs as default_codecs
from feincms3_api.conf import excluded_paths
from feincms3_api.errors import WireError
from feincms3_api.paths import iter_path, tokens


@dataclasses.dataclass
class Context:
    """Everything an adapter needs to know about one type in one request"""

    type: str
    model: type[models.Model]
    form_class: type[forms.BaseForm]
    read_only: tuple = ()
    exclude: frozenset = frozenset()
    codecs: object = default_codecs
    request: object = None

    @property
    def excluded_paths(self):
        return excluded_paths(self.type)


class RawWidget(forms.Widget):
    """
    Hands a value to the form field's ``clean()`` as it is

    The admin's widgets read HTML form data (a date and a time as
    ``name_0`` and ``name_1``, a checkbox as present or not). The API's
    values are already what the fields clean, so the forms get this widget.
    """

    def value_from_datadict(self, data, files, name):
        return data.get(name)

    def value_omitted_from_data(self, data, files, name):
        return name not in data


def current_data(form):
    """An unbound form's initial values, as data its fields can clean"""
    data = {}
    for name, field in form.fields.items():
        value = form.get_initial_for_field(field, name)
        if isinstance(field, forms.FileField):
            value = None  # Keeps the file
        elif isinstance(field, forms.MultiValueField):
            value = field.widget.decompress(value)
        elif isinstance(value, (list, tuple)):
            value = [getattr(item, "pk", item) for item in value]
        elif isinstance(value, models.Model):
            value = value.pk
        elif isinstance(value, dict):
            value = copy.deepcopy(value)  # Not the instance's own JSON
        data[name] = value
    return data


def prepare_form(form, context, *, keep=()):
    """
    Make a bound form take the API's data: fields the API sets itself (e.g.
    a plugin's parent) aren't the form's business unless they're in
    ``keep``, and the values are cleaned as they are (``RawWidget``)
    """
    for name in context.exclude:
        if name not in keep:
            form.fields.pop(name, None)
    for form_field in form.fields.values():
        form_field.widget = RawWidget()


def form_errors(form, path):
    """A bound form's errors as ``[{"path", "message"}]``"""
    return [
        {
            "path": path if name == forms.forms.NON_FIELD_ERRORS else f"{path}.{name}",
            "message": str(message),
        }
        for name, messages in form.errors.items()
        for message in messages
    ]


def _set_concrete(data, concrete, value):
    """Set ``value`` at a concrete path (``data.figures[0].unit``) if its
    parents exist"""
    keys = [
        int(index) if index else name
        for index, name in re.findall(r"\[(\d+)\]|([^.\[\]]+)", concrete)
    ]
    node = data
    for key in keys[:-1]:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return
    last = keys[-1]
    if (isinstance(node, dict) and isinstance(last, str)) or (
        isinstance(node, list) and isinstance(last, int) and last < len(node)
    ):
        node[last] = value


def resolve_upload(request, ref):
    """
    The uploaded file for a form, and its deletion once the write is
    committed (a dry run or a failed write keeps it for another try)
    """
    from feincms3_api.models import Upload  # noqa: PLC0415

    try:
        upload = Upload.objects.usable(request.user).get(pk=ref.id)
    except (Upload.DoesNotExist, ValidationError, ValueError) as exc:
        raise WireError("No such upload of yours, or it has expired.") from exc
    transaction.on_commit(upload.discard)
    return upload.as_file()


class FormAdapter:
    def fields(self, context):
        """``(fields, excluded)``: the exposed fields, and why others aren't"""
        exclude = context.exclude | {
            path for path in context.excluded_paths if len(tokens(path)) == 1
        }
        fields, excluded = [], {}
        for name, form_field in context.form_class.base_fields.items():
            if name in exclude:
                continue
            if context.codecs.lookup(form_field) is None:
                excluded[name] = f"no codec for {type(form_field).__name__}"
                continue
            read_only = name in context.read_only
            fields.append(Field(name, form_field, context.model, read_only=read_only))

        for name in context.read_only:
            if name in exclude or name in context.form_class.base_fields:
                continue
            try:
                model_field = context.model._meta.get_field(name)
            except FieldDoesNotExist:
                continue  # A method or an admin callable, not data
            form_field = getattr(model_field, "formfield", lambda: None)()
            if form_field is None or context.codecs.lookup(form_field) is None:
                continue
            fields.append(Field(name, form_field, context.model, read_only=True))
        return fields, excluded

    def to_wire(self, context, obj):
        fields, _excluded = self.fields(context)
        form = context.form_class(instance=obj) if obj is not None else None
        data = {}
        for field in fields:
            if field.read_only and field.model_field is not None:
                value = field.model_field.value_from_object(obj)
            else:
                value = form.get_initial_for_field(field.form_field, field.name)
            codec = context.codecs.lookup(field.form_field)
            data[field.name] = codec.to_wire(value, field)
        # The values may be the instance's own JSON data: remove from a copy.
        data = copy.deepcopy(data)
        _remove_paths(data, context.excluded_paths)
        return data

    def form_data(self, context, instance, wire, *, path, extra=None):
        """
        ``(form data, errors)``: the current values, with the wire fields
        decoded over them, and ``extra`` (form data, e.g. a plugin's region)

        A field which isn't sent keeps its value. Read-only fields may be
        sent unchanged, so what was read can be written back. Values inside
        JSON data which ``EXCLUDE_FIELDS`` hides are kept as they were, by
        their position.
        """
        errors = []
        if not isinstance(wire, dict):
            return None, [{"path": path, "message": "Expected an object."}]
        fields, _excluded = self.fields(context)
        by_name = {field.name: field for field in fields}
        unbound = context.form_class(instance=instance)
        data = current_data(unbound)
        current = (
            self.to_wire(context, instance)
            if wire and instance is not None and instance.pk is not None
            else {}
        )

        for name, value in wire.items():
            field_path = f"{path}.{name}"
            if (field := by_name.get(name)) is None:
                errors.append({"path": field_path, "message": "Unknown field."})
            elif field.read_only:
                if instance is None or value != current.get(name):
                    errors.append({"path": field_path, "message": "Read-only."})
            else:
                try:
                    data[name] = self.decode(context, field, value, data.get(name))
                except WireError as exc:
                    errors.append({"path": field_path, "message": str(exc)})

        data.update(extra or {})
        return data, errors

    def bind(self, context, instance, wire, *, path, extra=None):
        """``(bound form, errors)``, with ``form_data()``"""
        data, errors = self.form_data(context, instance, wire, path=path, extra=extra)
        if data is None:
            return None, errors
        form = context.form_class(data=data, files={}, instance=instance)
        prepare_form(form, context, keep=extra or ())
        return form, errors

    def decode(self, context, field, value, current):
        """
        One wire value as form data; ``current`` is the field's current form
        data, for the values inside JSON which ``EXCLUDE_FIELDS`` hides
        """
        decoded = context.codecs.lookup(field.form_field).from_wire(value, field)
        if isinstance(decoded, UploadRef):
            return resolve_upload(context.request, decoded)
        hidden = [
            path
            for path in context.excluded_paths
            if len(tokens(path)) > 1 and tokens(path)[0] == field.name
        ]
        if hidden and isinstance(decoded, (dict, list)):
            old, new = {field.name: current}, {field.name: decoded}
            for hidden_path in hidden:
                for concrete, container, key in iter_path(old, hidden_path):
                    _set_concrete(new, concrete, container[key])
            decoded = new[field.name]
        return decoded

    def json_schema(self, context):
        fields, excluded = self.fields(context)
        properties, required = {}, []
        for field in fields:
            schema = context.codecs.lookup(field.form_field).json_schema(field)
            if field.read_only:
                schema = {**schema, "readOnly": True}
            elif field.form_field.required:
                required.append(field.name)
            if title := field.form_field.label:
                schema.setdefault("title", str(title))
            if help_text := field.form_field.help_text:
                schema.setdefault("description", str(help_text))
            properties[field.name] = schema
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        for path in context.excluded_paths:
            _remove_schema_path(schema, path)
        if excluded:
            schema["x-feincms3-excluded"] = excluded
        return schema


def _remove_paths(data, paths):
    for path in paths:
        if len(tokens(path)) > 1:
            for _concrete, container, key in reversed(list(iter_path(data, path))):
                del container[key]


def _remove_schema_path(schema, path):
    *parents, last = tokens(path)
    node = schema
    for token in parents:
        if token is None:
            node = node.get("items", {})
        else:
            node = node.get("properties", {}).get(token, {})
    if last is not None:
        node.get("properties", {}).pop(last, None)
        if last in node.get("required", ()):
            node["required"].remove(last)


class AdapterRegistry:
    def __init__(self, default=None):
        self.default = default or FormAdapter()
        self._adapters = {}

    def register(self, type_label, adapter):
        self._adapters[type_label] = adapter

    def lookup(self, type_label):
        return self._adapters.get(type_label, self.default)
