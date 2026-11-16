"""
Resources: the models a project exposes, and how to read them

Everything comes from the model's admin: its queryset, permissions, form
fields and, for django-content-editor models, its plugin inlines.
"""

import hashlib
import json

from content_editor.admin import ContentEditorInline
from django.contrib import admin
from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Q
from django.urls import NoReverseMatch, reverse

from feincms3_api.adapters import Context
from feincms3_api.codecs import label
from feincms3_api.errors import ConflictError


STRUCTURAL_FIELDS = frozenset({"region", "ordering", "depth"})


def region_info(region):
    return {
        "key": region.key,
        "title": str(region.title),
        "inherited": bool(getattr(region, "inherited", False)),
    }


def allowed_types(inlines, region_keys):
    """``{region key: [plugin types]}`` as the inlines' ``regions`` say"""
    keys = set(region_keys)
    result = {key: [] for key in region_keys}
    for inline in inlines:
        regions = inline.regions
        if regions is None:
            allowed = keys
        elif callable(regions):
            allowed = set(regions(set(keys)))
        else:
            allowed = set(regions) & keys
        for key in region_keys:
            if key in allowed:
                result[key].append(label(inline.model))
    return result


def nest(plugins, containers):
    """
    A flat plugin list to a nested one: the plugins following a container
    (a type in ``containers``) with a greater depth are its ``children``
    """
    root = []
    stack = [(-1, root)]
    for plugin in plugins:
        plugin = dict(plugin)  # noqa: PLW2901
        depth = plugin.pop("depth", 0)
        while stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1].append(plugin)
        if plugin["type"] in containers:
            plugin["children"] = []
            stack.append((depth, plugin["children"]))
    return root


def _read_only(model_admin, request, obj):
    """
    The admin's read-only fields; all of them if the user may only view,
    which is what the admin shows (and its form then has no fields at all)
    """
    read_only = list(model_admin.get_readonly_fields(request, obj))
    if not model_admin.has_change_permission(request, obj):
        read_only.extend(
            name
            for name in flatten_fieldsets(model_admin.get_fieldsets(request, obj))
            if name not in read_only
        )
    return tuple(read_only)


def etag(wire):
    data = json.dumps(
        {"fields": wire["fields"], "content": wire.get("content")},
        sort_keys=True,
        default=str,
    )
    return f'"{hashlib.sha256(data.encode()).hexdigest()[:32]}"'


class Resource:
    """
    A model exposed through the API

    ``name`` is its URL segment (default: the model name with an ``s``,
    e.g. ``articles``), ``admin_site`` the admin site its
    ``ModelAdmin`` is registered with.
    """

    #: Fields which can be filtered on in lists, if the model has them;
    #: ``filters=`` replaces them
    filter_fields = ("language_code", "is_active", "translation_of", "site")

    def __init__(
        self, model, *, name=None, admin_site=None, title_field=None, filters=None
    ):
        self.model = model
        # Not the verbose name: URLs mustn't change with the language.
        self.name = name or f"{model._meta.model_name}s"
        self.admin_site = admin_site or admin.site
        self.title_field = title_field
        if filters is not None:
            self.filter_fields = tuple(filters)

    def __repr__(self):
        return f"<{type(self).__name__} {self.name} {self.type}>"

    @property
    def type(self):
        return label(self.model)

    @property
    def model_admin(self):
        try:
            return self.admin_site._registry[self.model]
        except KeyError as exc:
            raise ImproperlyConfigured(
                f"{self.type} isn't registered with the admin site"
                f" {self.admin_site.name!r}; the API takes its forms and"
                " permissions from there."
            ) from exc

    # Finding objects ########################################################

    def get_queryset(self, request):
        return self.model_admin.get_queryset(request)

    def filter_queryset(self, request, queryset, params):
        fields = {field.name: field for field in self.model._meta.get_fields()}
        for name in self.filter_fields:
            if name not in params or name not in fields:
                continue
            value = params[name]
            field = fields[name]
            if value in {"", "null"} and getattr(field, "null", False):
                queryset = queryset.filter(**{f"{name}__isnull": True})
            elif field.get_internal_type() == "BooleanField":
                queryset = queryset.filter(**{name: value.lower() in {"1", "true"}})
            elif field.is_relation:
                queryset = queryset.filter(**{f"{name}_id": value})
            else:
                queryset = queryset.filter(**{name: value})
        if q := params.get("q"):
            queryset, duplicates = self.model_admin.get_search_results(
                request, queryset, q
            )
            if duplicates:
                queryset = queryset.distinct()
        return queryset

    def get_object(self, request, pk):
        try:
            pk = self.model._meta.pk.to_python(pk)
            return self.get_queryset(request).get(pk=pk)
        except (ValidationError, self.model.DoesNotExist):
            return None

    # Permissions ############################################################

    def has_view_permission(self, request, obj=None):
        return self.model_admin.has_view_or_change_permission(request, obj)

    # Reading ################################################################

    def context(self, request, obj, api):
        model_admin = self.model_admin
        return Context(
            type=self.type,
            model=self.model,
            form_class=model_admin.get_form(request, obj, change=obj is not None),
            read_only=_read_only(model_admin, request, obj),
            codecs=api.codecs,
            request=request,
        )

    def plugin_inlines(self, request, obj=None):
        return [
            inline
            for inline in self.model_admin.get_inline_instances(request, obj)
            if isinstance(inline, ContentEditorInline)
        ]

    def plugin_context(self, request, inline, obj, api):
        return Context(
            type=label(inline.model),
            model=inline.model,
            form_class=inline.get_formset(request, obj).form,
            read_only=_read_only(inline, request, obj),
            exclude=STRUCTURAL_FIELDS | {inline.fk_name},
            codecs=api.codecs,
            request=request,
        )

    def regions(self, obj):
        return list(getattr(obj, "regions", None) or ())

    def region_sets(self):
        """
        The sets of regions an object can have: one per page type or
        template, or the model's own ``regions``
        """
        for attribute in ("TYPES", "TEMPLATES"):
            if types := getattr(self.model, attribute, None):
                return [
                    {
                        "key": type.key,
                        "title": str(type.title),
                        "regions": type.regions,
                    }
                    for type in types
                ]
        regions = getattr(self.model, "regions", None)
        if isinstance(regions, (list, tuple)):
            return [{"key": None, "title": None, "regions": regions}]
        return []

    def title(self, obj):
        if self.title_field:
            return str(getattr(obj, self.title_field))
        return str(obj)

    def url(self, request, obj):
        if not hasattr(obj, "get_absolute_url"):
            return None
        try:
            url = obj.get_absolute_url()
        except NoReverseMatch:
            return None
        return request.build_absolute_uri(url) if url else None

    def admin_url(self, obj):
        opts = self.model._meta
        try:
            return reverse(
                f"{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change",
                args=[obj.pk],
            )
        except NoReverseMatch:
            return None

    def meta(self, request, obj, wire):
        meta = {
            "title": self.title(obj),
            "url": self.url(request, obj),
            "admin_url": self.admin_url(obj),
            "etag": etag(wire),
        }
        if regions := self.regions(obj):
            meta["regions"] = [region_info(region) for region in regions]
        return meta

    def plugins(self, inlines, obj):
        """
        ``(visible, hidden)``: the object's plugins in order, those with an
        inline (as ``(plugin, inline)``) and those without
        """
        by_model = {inline.model: inline for inline in inlines}
        lookups = {}
        for inline in inlines:
            lookups.setdefault(inline.model._meta.concrete_model, inline.fk_name)

        plugins = []
        if obj.pk is not None:
            for concrete, fk_name in lookups.items():
                if hasattr(concrete, "get_queryset"):
                    queryset = concrete.get_queryset()
                else:  # pragma: no cover
                    queryset = concrete._default_manager.all()
                plugins.extend(queryset.filter(Q(**{fk_name: obj})))

        plugins.sort(key=lambda plugin: (plugin.region, plugin.ordering, plugin.pk))
        visible = [(p, by_model[type(p)]) for p in plugins if type(p) in by_model]
        hidden = [p for p in plugins if type(p) not in by_model]
        return visible, hidden

    def read_content(self, request, obj, api, warnings):
        """``(content, container types)``, the content flat with depths"""
        inlines = self.plugin_inlines(request, obj)
        if not inlines:
            return None, set()
        by_model = {inline.model: inline for inline in inlines}
        visible, hidden = self.plugins(inlines, obj)
        if hidden:
            warnings.append(
                f"{len(hidden)} plugin(s) aren't shown: no inline for their type,"
                " or no permission to see it."
            )
        plugins = [plugin for plugin, _inline in visible]

        contexts = {}
        by_region = {}
        for plugin in plugins:
            inline = by_model[type(plugin)]
            if inline not in contexts:
                contexts[inline] = self.plugin_context(request, inline, obj, api)
            context = contexts[inline]
            by_region.setdefault(plugin.region, []).append(
                {
                    "type": context.type,
                    "id": plugin.pk,
                    "depth": getattr(plugin, "depth", 0),
                    "fields": api.adapters.lookup(context.type).to_wire(
                        context, plugin
                    ),
                }
            )

        region_keys = [region.key for region in self.regions(obj)]
        content = {key: [] for key in region_keys}
        for key, items in by_region.items():
            if key not in content:
                warnings.append(
                    f"Region {key!r} isn't one of this object's regions;"
                    " its plugins are shown, but not rendered."
                )
            content[key] = items
        containers = {
            label(model) for model in by_model if getattr(model, "container", False)
        }
        return content, containers

    def read(self, request, obj, api, *, content=True, nested=False):
        context = self.context(request, obj, api)
        warnings = []
        wire = {
            "type": self.type,
            "id": obj.pk,
            "fields": api.adapters.lookup(self.type).to_wire(context, obj),
        }
        containers = set()
        if content:
            data, containers = self.read_content(request, obj, api, warnings)
            if data is not None:
                wire["content"] = data
        # The ETag is computed from the flat form, whatever is returned.
        wire["meta"] = self.meta(request, obj, wire) | {"warnings": warnings}
        if nested and "content" in wire:
            wire["content"] = {
                key: nest(plugins, containers)
                for key, plugins in wire["content"].items()
            }
        return wire

    def get_plugin(self, request, obj, type_label, plugin_id):
        """``(plugin, inline)`` of one plugin of ``obj``, or ``None``"""
        for inline in self.plugin_inlines(request, obj):
            if label(inline.model) != type_label:
                continue
            model = inline.model
            queryset = model._meta.concrete_model.get_queryset()
            if hasattr(model, "TYPE"):
                queryset = queryset.filter(type=model.TYPE)
            try:
                plugin = queryset.get(
                    **{inline.fk_name: obj, "pk": model._meta.pk.to_python(plugin_id)}
                )
            except (ValidationError, model.DoesNotExist):
                return None
            return plugin, inline
        return None

    def read_plugin(self, request, obj, api, type_label, plugin_id):
        if (found := self.get_plugin(request, obj, type_label, plugin_id)) is None:
            return None
        plugin, inline = found
        context = self.plugin_context(request, inline, obj, api)
        return {
            "type": type_label,
            "id": plugin.pk,
            "region": plugin.region,
            "depth": getattr(plugin, "depth", 0),
            "fields": api.adapters.lookup(type_label).to_wire(context, plugin),
        }

    def check_delete(self, request, obj):
        """Raise ``ConflictError`` if ``obj`` mustn't be deleted"""

    def validate_instance(self, obj):
        """
        Errors (``[{"path", "message"}]``) the admin's form doesn't find,
        for ``obj`` as it will be saved
        """
        return []

    #: Whether objects can be found by their path (``find/?path=``)
    findable = False

    def find(self, request, params):
        """The object at ``params["path"]``, or ``None``"""
        return


class ObjectResource(Resource):
    """Any model, with or without django-content-editor plugins"""


class PageResource(Resource):
    """feincms3 pages: a tree, with paths"""

    filter_fields = (*Resource.filter_fields, "parent", "page_type", "path")
    findable = True

    def path_scope(self):
        """
        The fields ``path`` is unique together with, from the model:
        ``()`` (feincms3), ``("site",)`` (feincms3-sites) or
        ``("language_code",)`` (feincms3-language-sites)
        """
        for fields in self.model._meta.unique_together:
            if "path" in fields:
                return tuple(field for field in fields if field != "path")
        return ()

    def scope_for_host(self, host):
        """The filter for the pages served on ``host``, ``None`` if none are"""
        scope = self.path_scope()
        if scope == ("site",):
            from feincms3_sites.utils import get_site_model  # noqa: PLC0415

            site = get_site_model()._default_manager.for_host(host)
            return None if site is None else {"site": site}
        if scope == ("language_code",):
            from feincms3_language_sites.models import site_for_host  # noqa: PLC0415

            site = site_for_host(host)
            return None if site is None else {"language_code": site["language_code"]}
        return {}

    def find(self, request, params):
        path = params.get("path") or ""
        queryset = self.get_queryset(request).filter(path=path)
        if host := params.get("host"):
            if (scope := self.scope_for_host(host)) is None:
                return None
            queryset = queryset.filter(**scope)
        pages = list(queryset[:2])
        if len(pages) > 1:
            raise ConflictError(
                "Several pages have this path, one per site or language; add"
                " the host (host=…).",
                pages=[{"type": self.type, "id": page.pk} for page in pages],
            )
        return pages[0] if pages else None

    def meta(self, request, obj, wire):
        return {"path": obj.path, **super().meta(request, obj, wire)}

    def check_delete(self, request, obj):
        children = self.model._default_manager.filter(parent=obj)
        if children.exists():
            raise ConflictError(
                "The page has subpages; delete or move them first.",
                children=[
                    {"type": self.type, "id": child.pk, "title": str(child)}
                    for child in children
                ],
            )
