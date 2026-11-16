"""
Adapters decide what an object's or a plugin's ``fields`` are

The default adapter is the admin's form: its fields, through the codecs.
That covers every plugin made of ordinary fields without any code. Projects
register their own adapter for a type only to change the shape.
"""

import dataclasses

from django import forms
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from feincms3_api.codecs import Field, codecs as default_codecs
from feincms3_api.conf import excluded_paths
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

    @property
    def excluded_paths(self):
        return excluded_paths(self.type)


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
        _remove_paths(data, context.excluded_paths)
        return data

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
