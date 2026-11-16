"""Reading: lists, objects, plugins"""

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
            items.append(wire)
        return {"count": queryset.count(), "items": items}

    def _get(self, request, name, pk):
        resource = self.resource(name)
        self._check_view(request, resource)
        if (obj := resource.get_object(request, pk)) is None:
            raise NotFoundError(f"No {resource.type} with id {pk!r}.")
        self._check_view(request, resource, obj)
        return resource, obj

    def detail(self, request, name, pk, *, nested=False):
        resource, obj = self._get(request, name, pk)
        return resource.read(request, obj, self, nested=nested)

    def plugin(self, request, name, pk, type_label, plugin_id):
        resource, obj = self._get(request, name, pk)
        wire = resource.read_plugin(request, obj, self, type_label, plugin_id)
        if wire is None:
            raise NotFoundError(
                f"No {type_label} with id {plugin_id!r} on this object."
            )
        return wire


def _bool(value):
    return (value or "").lower() in {"1", "true"}


def _int(value, default):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default
