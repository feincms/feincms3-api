"""
The content API: registered resources, the operations on them, and the
django-ninja router exposing them

The operations are plain methods taking a request (for the admin's methods,
which need one) and returning wire data. The endpoints only wrap them, so
management commands and other code can use them without HTTP.
"""

import functools

from django.http import HttpRequest, HttpResponse
from django.utils.functional import cached_property
from ninja import NinjaAPI, Router
from ninja.errors import HttpError

from feincms3_api.adapters import AdapterRegistry
from feincms3_api.auth import default_auth
from feincms3_api.codecs import codecs as default_codecs, label
from feincms3_api.conf import config
from feincms3_api.resources import allowed_types, region_info


_instances = []


class NotFoundError(Exception):
    pass


class ForbiddenError(Exception):
    pass


class ContentAPI:
    def __init__(self, *, auth=None, codecs=None, adapters=None):
        self.auth = auth if auth is not None else default_auth()
        self.codecs = codecs or default_codecs.copy()
        self.adapters = adapters or AdapterRegistry()
        self._resources = {}
        _instances.append(self)

    # Registration ###########################################################

    def register(self, resource):
        if "router" in self.__dict__:
            raise RuntimeError("Register resources before using the router.")
        if resource.name in self._resources or resource.name == "schema":
            raise ValueError(f"The resource name {resource.name!r} is taken.")
        self._resources[resource.name] = resource
        return resource

    @property
    def resources(self):
        return list(self._resources.values())

    def resource(self, name):
        try:
            return self._resources[name]
        except KeyError as exc:
            raise NotFoundError(f"No resource {name!r}.") from exc

    # Operations #############################################################

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

    def schema(self, request):
        resources, types = [], {}
        for resource in self.resources:
            if not resource.has_view_permission(request):
                continue
            inlines = resource.plugin_inlines(request)
            region_sets = []
            for region_set in resource.region_sets():
                keys = [region.key for region in region_set["regions"]]
                allowed = allowed_types(inlines, keys)
                region_sets.append(
                    {
                        "key": region_set["key"],
                        "title": region_set["title"],
                        "regions": [
                            region_info(region) | {"plugins": allowed[region.key]}
                            for region in region_set["regions"]
                        ],
                    }
                )
            resources.append(
                {
                    "name": resource.name,
                    "type": resource.type,
                    "title": str(resource.model._meta.verbose_name),
                    "region_sets": region_sets,
                }
            )
            types[resource.type] = {
                "title": str(resource.model._meta.verbose_name),
                "resource": resource.name,
            }
            for inline in inlines:
                types[label(inline.model)] = {
                    "title": str(inline.model._meta.verbose_name),
                    "plugin_of": resource.type,
                    "container": bool(getattr(inline.model, "container", False)),
                }
        return {"resources": resources, "types": types}

    def contexts(self, request):
        """``(type, context)`` of every type the user may see"""
        for resource in self.resources:
            if not resource.has_view_permission(request):
                continue
            yield resource.type, resource.context(request, None, self)
            for inline in resource.plugin_inlines(request):
                yield (
                    label(inline.model),
                    resource.plugin_context(request, inline, None, self),
                )

    def type_schema(self, request, type_label):
        for type, context in self.contexts(request):
            if type == type_label:
                schema = self.adapters.lookup(type).json_schema(context)
                return {"$schema": "https://json-schema.org/draft/2020-12/schema"} | (
                    schema
                )
        raise NotFoundError(f"No type {type_label!r}.")

    # HTTP ###################################################################

    @cached_property
    def router(self):
        router = Router(auth=self.auth, tags=["content"])

        @router.get("schema/", operation_id="schema")
        @_errors
        def schema(request):
            """Resources, their region sets and the plugins allowed per region"""
            return self.schema(request)

        @router.get("schema/{type_label}/", operation_id="type_schema")
        @_errors
        def type_schema(request, type_label: str):
            """The JSON schema of one type's ``fields``"""
            return self.type_schema(request, type_label)

        for resource in self.resources:
            self._add_routes(router, resource.name)
        return router

    def _add_routes(self, router, name):
        operation = name.replace("-", "_")

        @router.get(f"{name}/", operation_id=f"{operation}_list")
        @_errors
        def list_objects(request: HttpRequest):
            """List: ``limit``, ``offset``, ``q``, filters, ``content``, ``fields``"""
            return self.list(request, name, request.GET)

        @router.get(f"{name}/{{pk}}/", operation_id=f"{operation}_detail")
        @_errors
        def detail(request, pk: str, response: HttpResponse):
            """One object with its content; ``nested=true`` for a tree"""
            wire = self.detail(
                request, name, pk, nested=_bool(request.GET.get("nested"))
            )
            response["ETag"] = wire["meta"]["etag"]
            return wire

        @router.get(
            f"{name}/{{pk}}/plugins/{{type_label}}/{{plugin_id}}/",
            operation_id=f"{operation}_plugin",
        )
        @_errors
        def plugin(request, pk: str, type_label: str, plugin_id: str):
            """One plugin of an object"""
            return self.plugin(request, name, pk, type_label, plugin_id)

    def as_api(self, **kwargs):
        """A ``NinjaAPI`` of its own, for projects without one"""
        kwargs.setdefault("title", "Content API")
        kwargs.setdefault("urls_namespace", "feincms3_api")
        api = NinjaAPI(**kwargs)
        api.add_router("/", self.router)
        return api


def _bool(value):
    return (value or "").lower() in {"1", "true"}


def _int(value, default):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotFoundError as exc:
            raise HttpError(404, str(exc)) from exc
        except ForbiddenError as exc:
            raise HttpError(403, str(exc)) from exc

    return wrapper
