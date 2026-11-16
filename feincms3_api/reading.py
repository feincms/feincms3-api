"""Reading: lists, objects, plugins"""

from feincms3_api import references
from feincms3_api.codecs import label
from feincms3_api.conf import config
from feincms3_api.errors import ForbiddenError, NotFoundError


class ReadingMixin:
    def _check_view(self, request, resource, obj=None):
        if not resource.has_view_permission(request, obj):
            raise ForbiddenError(f"No permission to view {resource.type}.")

    def list(self, request, name, params):
        resource = self.resource(name)
        self._check_view(request, resource)
        queryset = resource.filter_queryset(
            request, resource.get_queryset(request), params
        )
        limit = min(
            _int(params.get("limit"), config()["DEFAULT_PAGE_SIZE"]),
            config()["MAX_PAGE_SIZE"],
        )
        offset = _int(params.get("offset"), 0)
        content = _bool(params.get("content"))
        only = [field for field in params.get("fields", "").split(",") if field]
        items = []
        for obj in queryset[offset : offset + limit]:
            wire = resource.read(
                request, obj, self, content=content, nested=_bool(params.get("nested"))
            )
            if only:
                wire["fields"] = {
                    key: value for key, value in wire["fields"].items() if key in only
                }
            items.append((obj, wire))
        if expands_references(params):
            self._expand(request, resource, items)
        return {"count": queryset.count(), "items": [wire for _obj, wire in items]}

    def _expand(self, request, resource, items):
        """``meta.references`` of ``[(obj, wire)]``, looked up together"""
        refs = [
            references.collect_object(self, request, resource, obj, wire)
            for obj, wire in items
        ]
        expanded = references.expand(self, request, set().union(*refs))
        for (_obj, wire), own in zip(items, refs, strict=True):
            subset = {}
            for type_label, pk in sorted(own, key=str):
                key = str(pk)
                subset.setdefault(type_label, {})[key] = expanded.get(
                    type_label, {}
                ).get(key)
            wire["meta"]["references"] = subset

    def _get(self, request, name, pk):
        resource = self.resource(name)
        self._check_view(request, resource)
        if (obj := resource.get_object(request, pk)) is None:
            raise NotFoundError(f"No {resource.type} with id {pk!r}.")
        self._check_view(request, resource, obj)
        return resource, obj

    def find(self, request, name, params):
        """An object by its path (pages)"""
        resource = self.resource(name)
        self._check_view(request, resource)
        if (obj := resource.find(request, params)) is None:
            raise NotFoundError(f"No {resource.type} at {params.get('path')!r}.")
        self._check_view(request, resource, obj)
        wire = resource.read(request, obj, self, nested=_bool(params.get("nested")))
        if expands_references(params):
            self._expand(request, resource, [(obj, wire)])
        return wire

    def detail(self, request, name, pk, *, nested=False, expand=False):
        """One object; ``expand`` adds ``meta.references``"""
        resource, obj = self._get(request, name, pk)
        wire = resource.read(request, obj, self, nested=nested)
        if expand:
            self._expand(request, resource, [(obj, wire)])
        return wire

    def plugin(self, request, name, pk, type_label, plugin_id, *, expand=False):
        resource, obj = self._get(request, name, pk)
        wire = resource.read_plugin(request, obj, self, type_label, plugin_id)
        if wire is None:
            raise NotFoundError(
                f"No {type_label} with id {plugin_id!r} on this object."
            )
        if expand:
            context = next(
                resource.inline_context(request, inline, obj, self)
                for inline in resource.plugin_inlines(request, obj)
                if label(inline.model) == type_label
            )
            wire["meta"] = {
                "references": references.expand(
                    self,
                    request,
                    set(references.collect(self, context, wire["fields"])),
                )
            }
        return wire


def expands_references(params):
    return "references" in (params.get("expand") or "").split(",")


def _bool(value):
    return (value or "").lower() in {"1", "true"}


def _int(value, default):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default
