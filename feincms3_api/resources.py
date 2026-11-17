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
from django.db.models import Q, Value
from django.forms.models import _get_foreign_key
from django.urls import NoReverseMatch, reverse

from feincms3_api.adapters import Context
from feincms3_api.codecs import label
from feincms3_api.errors import APIError, ConflictError


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


WARNING, ERROR = "warning", "error"


def warning(message, *, level=WARNING, path=None):
    """
    One entry of ``meta.warnings``: ``level`` is ``"warning"`` or ``"error"``
    (it won't work until fixed), ``path`` where the problem is, if somewhere
    in particular (``fields.name``, ``content.main[2]``)
    """
    entry = {"level": level, "message": str(message)}
    if path is not None:
        entry["path"] = path
    return entry


def _fetch_plugins(request, inlines, objs):
    """``{pk: [(plugin, inline)]}`` of ``objs``, in order"""
    result = {obj.pk: [] for obj in objs}
    for inline in inlines:
        attname = inline.model._meta.get_field(inline.fk_name).attname
        for plugin in inline.get_queryset(request).filter(
            Q(**{f"{inline.fk_name}__in": objs})
        ):
            result[getattr(plugin, attname)].append((plugin, inline))
    for plugins in result.values():
        plugins.sort(key=lambda pair: _plugin_order(pair[0]))
    return result


def _plugin_order(plugin):
    return (plugin.region, plugin.ordering, plugin.pk)


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


def fk_name(inline):
    """The inline's foreign key to its parent, as its formset finds it"""
    return inline.fk_name or _get_foreign_key(inline.parent_model, inline.model).name


def etag(wire):
    data = json.dumps(
        {
            "fields": wire["fields"],
            "content": wire.get("content"),
            "inlines": wire.get("inlines"),
        },
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

    #: Fields a create must give, although the model has a default;
    #: ``required_on_create=`` replaces them
    required_on_create = ()

    def __init__(
        self,
        model,
        *,
        name=None,
        admin_site=None,
        title_field=None,
        filters=None,
        required_on_create=None,
    ):
        self.model = model
        # Not the verbose name: URLs mustn't change with the language.
        self.name = name or f"{model._meta.model_name}s"
        self.admin_site = admin_site or admin.site
        self.title_field = title_field
        if filters is not None:
            self.filter_fields = tuple(filters)
        if required_on_create is not None:
            self.required_on_create = tuple(required_on_create)

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
            if not self.searchable(request):
                raise APIError(
                    f"{self.name} can't be searched: the admin of"
                    f" {self.type} has no search_fields."
                )
            queryset, duplicates = self.model_admin.get_search_results(
                request, queryset, q
            )
            if duplicates:
                queryset = queryset.distinct()
        return queryset

    def filters(self):
        """The filters lists take: ``filter_fields`` the model has"""
        names = {field.name for field in self.model._meta.get_fields()}
        return [name for name in self.filter_fields if name in names]

    def searchable(self, request):
        """Whether lists take ``q``: the admin's ``search_fields``"""
        return bool(self.model_admin.get_search_fields(request))

    def get_object(self, request, pk):
        try:
            pk = self.model._meta.pk.to_python(pk)
            return self.get_queryset(request).get(pk=pk)
        except (ValidationError, self.model.DoesNotExist):
            return None

    # Permissions ############################################################

    def required_fields(self, request, api):
        """``required_on_create`` without the fields the user can't write"""
        if not self.required_on_create:
            return []
        context = self.context(request, None, api)
        properties = api.adapters.lookup(self.type).json_schema(context)["properties"]
        return [
            name
            for name in self.required_on_create
            if name in properties and not properties[name].get("readOnly")
        ]

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
        """The admin's django-content-editor inlines: the plugins"""
        return [
            inline
            for inline in self.model_admin.get_inline_instances(request, obj)
            if isinstance(inline, ContentEditorInline)
        ]

    def other_inlines(self, request, obj=None):
        """The admin's other inlines: ``inlines`` on the wire"""
        return [
            inline
            for inline in self.model_admin.get_inline_instances(request, obj)
            if not isinstance(inline, ContentEditorInline)
        ]

    def inline_context(self, request, inline, obj, api):
        """The context of a plugin's or another inline's ``fields``"""
        exclude = {fk_name(inline)}
        if isinstance(inline, ContentEditorInline):
            exclude |= STRUCTURAL_FIELDS
        return Context(
            type=label(inline.model),
            model=inline.model,
            form_class=inline.get_formset(request, obj).form,
            read_only=_read_only(inline, request, obj),
            exclude=frozenset(exclude),
            codecs=api.codecs,
            request=request,
        )

    def inline_objects(self, request, inline, obj):
        """The objects of another inline, as the admin's formset has them"""
        if obj is None or obj.pk is None:
            return []
        queryset = inline.get_queryset(request).filter(**{fk_name(inline): obj})
        if not queryset.ordered:
            queryset = queryset.order_by(inline.model._meta.pk.name)
        return list(queryset)

    def read_inlines(self, request, obj, api):
        """``{type: [{"id", "fields"}]}`` of the other inlines, or ``None``"""
        inlines = self.other_inlines(request, obj)
        if not inlines:
            return None
        result = {}
        for inline in inlines:
            context = self.inline_context(request, inline, obj, api)
            adapter = api.adapters.lookup(context.type)
            result[context.type] = [
                {"id": item.pk, "fields": adapter.to_wire(context, item)}
                for item in self.inline_objects(request, inline, obj)
            ]
        return result

    def regions(self, obj):
        return list(getattr(obj, "regions", None) or ())

    def region_sets(self):
        """
        The sets of regions an object can have: one per page type, template
        or form type (feincms3-forms), or the model's own ``regions``. A form
        type's regions can depend on the form; those are left out.
        """
        for attribute in ("TYPES", "TEMPLATES", "FORMS"):
            if types := getattr(self.model, attribute, None):
                return [
                    {
                        "key": type.key,
                        "title": str(getattr(type, "title", None) or type.label),
                        "regions": [] if callable(type.regions) else type.regions,
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

    def describe(self, request, obj):
        """
        What a reader needs to show a reference to ``obj`` without reading
        it: in its ``meta``, and in ``meta.references`` of what refers to it
        """
        return {
            "title": self.title(obj),
            "url": self.url(request, obj),
            "admin_url": self.admin_url(obj),
        }

    def meta(self, request, obj, wire):
        meta = self.describe(request, obj) | {"etag": etag(wire)}
        if regions := self.regions(obj):
            meta["regions"] = [region_info(region) for region in regions]
        return meta

    def plugins(self, request, inlines, obj):
        """
        The object's plugins in order, as ``(plugin, inline)``

        The inlines' querysets decide, as in the admin: feincms3-forms'
        proxies share a table, and each inline shows only its own type.
        """
        if obj.pk is None:
            return []
        return _fetch_plugins(request, inlines, [obj])[obj.pk]

    def plugins_of(self, request, objs):
        """``{pk: plugins(...)}`` of several objects, one query per inline"""
        groups = {}
        for obj in objs:
            inlines = self.plugin_inlines(request, obj)
            key = tuple(type(inline) for inline in inlines)
            groups.setdefault(key, (inlines, []))[1].append(obj)
        result = {}
        for inlines, members in groups.values():
            result |= _fetch_plugins(request, inlines, members)
        return result

    def count_hidden_plugins(self, inlines, obj, visible):
        """
        How many plugins of ``obj`` in the inlines' tables no inline shows,
        in one query
        """
        if obj.pk is None:
            return 0
        shown = {}
        for plugin, _inline in visible:
            shown.setdefault(plugin._meta.concrete_model, set()).add(plugin.pk)
        lookups = {}
        for inline in inlines:
            lookups.setdefault(inline.model._meta.concrete_model, inline.fk_name)
        querysets = [
            concrete._base_manager.filter(Q(**{fk_name: obj}))
            .exclude(pk__in=shown.get(concrete, ()))
            .order_by()
            .values_list(Value(1))
            for concrete, fk_name in lookups.items()
        ]
        if not querysets:
            return 0
        return querysets[0].union(*querysets[1:], all=True).count()

    def read_content(self, request, obj, api, warnings, *, plugins=None):
        """
        ``(content, container types)``, the content flat with depths;
        ``plugins`` are the object's from ``plugins_of()``, if fetched already
        """
        inlines = self.plugin_inlines(request, obj)
        if not inlines:
            return None, set()
        by_model = {inline.model: inline for inline in inlines}
        visible = self.plugins(request, inlines, obj) if plugins is None else plugins
        if hidden := self.count_hidden_plugins(inlines, obj, visible):
            warnings.append(
                warning(
                    f"{hidden} plugin(s) aren't shown: no inline for their"
                    " type, or no permission to see it.",
                    path="content",
                )
            )
        plugins = [plugin for plugin, _inline in visible]

        contexts = {}
        by_region = {}
        for plugin in plugins:
            inline = by_model[type(plugin)]
            if inline not in contexts:
                contexts[inline] = self.inline_context(request, inline, obj, api)
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
                    warning(
                        f"Region {key!r} isn't one of this object's regions;"
                        " its plugins are shown, but not rendered.",
                        path=f"content.{key}",
                    )
                )
            content[key] = items
        containers = {
            label(model) for model in by_model if getattr(model, "container", False)
        }
        return content, containers

    def read(self, request, obj, api, *, content=True, nested=False, plugins=None):
        context = self.context(request, obj, api)
        warnings = []
        wire = {
            "type": self.type,
            "id": obj.pk,
            "fields": api.adapters.lookup(self.type).to_wire(context, obj),
        }
        containers = set()
        if content:
            data, containers = self.read_content(
                request, obj, api, warnings, plugins=plugins
            )
            if data is not None:
                wire["content"] = data
            if (inlines := self.read_inlines(request, obj, api)) is not None:
                wire["inlines"] = inlines
        warnings.extend(self.check(request, obj))
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
            queryset = inline.get_queryset(request)
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
        context = self.inline_context(request, inline, obj, api)
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

    def check(self, request, obj):
        r"""
        Problems which don't stop saving, as ``warning()``\s: the object
        is saved, but may not work as intended

        Returned in ``meta.warnings`` of every read, and so of every write's
        response (dry runs included), after the content's own warnings.
        """
        return []

    def cache_keys(self, request, obj):
        """
        The cache keys of ``obj``'s rendered content, which
        ``DELETE …/cache/`` deletes: django-content-editor's default for
        ``regions_from_item(..., timeout=...)``
        """
        return [f"regions-{obj._meta.label_lower}-{obj.pk}"]

    #: Whether objects can be found by their path (``find/?path=``)
    findable = False

    def find(self, request, params):
        """The object at ``params["path"]``, or ``None``"""
        return


class ObjectResource(Resource):
    """Any model, with or without django-content-editor plugins"""


class TreeResource(Resource):
    """
    A django-tree-queries tree: pages, chapters of a book, categories

    Moved by changing ``parent`` (and ``position``, if the model has one).
    Deleting a node with children is refused instead of deleting them too.
    """

    filter_fields = (*Resource.filter_fields, "parent")

    def check_delete(self, request, obj):
        children = self.model._default_manager.filter(parent=obj)
        if children.exists():
            raise ConflictError(
                f"The {self.model._meta.verbose_name} has children;"
                " delete or move them first.",
                children=[
                    {"type": self.type, "id": child.pk, "title": str(child)}
                    for child in children
                ],
            )


class PageResource(TreeResource):
    """feincms3 pages: a tree, with paths"""

    filter_fields = (*TreeResource.filter_fields, "page_type", "path")
    # feincms3 defaults to active, so a client forgetting it would publish.
    required_on_create = ("is_active",)
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

    def describe(self, request, obj):
        return {"path": obj.path, **super().describe(request, obj)}
