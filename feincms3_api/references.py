"""
Expanding references: what an object refers to, described

``?expand=references`` adds ``meta.references``, ``{type: {id: description}}``
of every reference in the object's ``fields``, plugins and inlines, so a
reader doesn't need one request per reference. References stay as they are
in ``fields``, so what was read can be written back.

A registered resource describes its objects (``Resource.describe()``:
title, URL, admin URL, a page's path, a file's name and size); another model
registered in the admin is described by its title and admin URL. Only
objects the user may see are described, the others (and those which don't
exist) are ``null``.
"""

import contextlib
from functools import partial

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch, reverse


def collect(api, context, fields):
    """``(type, id)`` of the references in the wire ``fields`` of a type"""
    adapter_fields, _excluded = api.adapters.lookup(context.type).fields(context)
    for field in adapter_fields:
        if field.name in fields:
            codec = context.codecs.lookup(field.form_field)
            yield from codec.references(fields[field.name], field)


def collect_object(api, request, resource, obj, wire):
    """``(type, id)`` of every reference in an object's wire data"""
    refs = set(collect(api, resource.context(request, obj, api), wire["fields"]))
    contexts = {}

    def context(type_label):
        if type_label not in contexts:
            inline = next(
                inline
                for inline in [
                    *resource.plugin_inlines(request, obj),
                    *resource.other_inlines(request, obj),
                ]
                if _label(inline.model) == type_label
            )
            contexts[type_label] = resource.inline_context(request, inline, obj, api)
        return contexts[type_label]

    def plugins(items):
        for item in items:
            refs.update(collect(api, context(item["type"]), item["fields"]))
            plugins(item.get("children", ()))

    for items in (wire.get("content") or {}).values():
        plugins(items)
    for type_label, items in (wire.get("inlines") or {}).items():
        for item in items:
            refs.update(collect(api, context(type_label), item["fields"]))
    return refs


def expand(api, request, refs):
    """``{type: {id: description or None}}`` of the references ``refs``"""
    by_type = {}
    for type_label, pk in refs:
        by_type.setdefault(type_label, set()).add(pk)
    result = {}
    for type_label, pks in sorted(by_type.items()):
        try:
            model = apps.get_model(type_label)
        except (LookupError, ValueError):
            continue
        found = {}
        describe, queryset = _describer(api, request, model)
        if queryset is not None:
            valid = []
            for pk in pks:
                with contextlib.suppress(ValidationError):
                    valid.append(model._meta.pk.to_python(pk))
            found = {
                str(obj.pk): describe(obj) for obj in queryset.filter(pk__in=valid)
            }
        result[type_label] = {
            str(pk): found.get(str(pk)) for pk in sorted(pks, key=str)
        }
    return result


def _describer(api, request, model):
    """``(describe, queryset)`` for ``model``, or ``(None, None)``"""
    for resource in api.resources:
        if resource.model is model:
            if not resource.has_view_permission(request):
                return None, None
            return (
                partial(resource.describe, request),
                resource.get_queryset(request),
            )
    if (model_admin := admin.site._registry.get(model)) is not None:
        if not model_admin.has_view_permission(request):
            return None, None
        return (
            lambda obj: {"title": str(obj), "admin_url": _admin_url(obj)},
            model_admin.get_queryset(request),
        )
    return None, None


def _admin_url(obj):
    opts = obj._meta
    try:
        return reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk]
        )
    except NoReverseMatch:
        return None


def _label(model):
    return model._meta.label_lower
