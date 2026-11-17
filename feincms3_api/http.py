"""The django-ninja router around the operations"""

import functools
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import File, Router, Schema, UploadedFile

from feincms3_api.errors import APIError
from feincms3_api.reading import _bool, expands_references


class ObjectIn(Schema):
    fields: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    inlines: dict[str, Any] | None = None


class ContentIn(Schema):
    content: dict[str, Any]


class PluginIn(Schema):
    fields: dict[str, Any]


def build_router(api):
    router = Router(auth=api.auth, tags=["content"])

    @router.get("schema/", operation_id="schema")
    @_errors
    def schema(request):
        """Resources, their region sets and the plugins allowed per region"""
        return api.schema(request)

    @router.get("schema/{type_label}/", operation_id="type_schema")
    @_errors
    def type_schema(request, type_label: str):
        """The JSON schema of one type's ``fields``"""
        return api.type_schema(request, type_label)

    @router.post("uploads/", operation_id="upload")
    @_errors
    def upload(request, file: File[UploadedFile]):
        """Stage a file (multipart, field ``file``); name it in a write as
        ``{"upload": id}``"""
        return JsonResponse(api.upload(request, file), status=201)

    for resource in api.resources:
        _add_routes(api, router, resource)
    return router


def _add_routes(api, router, resource):
    name = resource.name
    operation = name.replace("-", "_")

    if resource.findable:
        # Before {pk}/, which would take "find" for an id
        @router.get(f"{name}/find/", operation_id=f"{operation}_find")
        @_errors
        def find(request: HttpRequest):
            """One object by its ``path``"""
            return _json(api.find(request, name, request.GET))

    @router.get(f"{name}/", operation_id=f"{operation}_list")
    @_errors
    def list_objects(request: HttpRequest):
        """List: ``limit``, ``offset``, ``q``, filters, ``content``, ``fields``"""
        return api.list(request, name, request.GET)

    @router.get(f"{name}/{{pk}}/", operation_id=f"{operation}_detail")
    @_errors
    def detail(request, pk: str, response: HttpResponse):
        """One object with its content; ``nested=true`` for a tree"""
        wire = api.detail(
            request,
            name,
            pk,
            nested=_bool(request.GET.get("nested")),
            expand=expands_references(request.GET),
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
        return api.plugin(
            request,
            name,
            pk,
            type_label,
            plugin_id,
            expand=expands_references(request.GET),
        )

    @router.post(f"{name}/", operation_id=f"{operation}_create")
    @_errors
    def create(request, payload: ObjectIn, dry_run: bool = False):
        """Create an object, with its content if given"""
        wire = api.create(
            request, name, payload.model_dump(exclude_unset=True), dry_run=dry_run
        )
        return _json(wire, 200 if dry_run else 201)

    @router.patch(f"{name}/{{pk}}/", operation_id=f"{operation}_update")
    @_errors
    def update(request, pk: str, payload: ObjectIn, dry_run: bool = False):
        """Change fields, and replace the regions given in ``content``"""
        wire = api.update(
            request,
            name,
            pk,
            payload.model_dump(exclude_unset=True),
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.put(f"{name}/{{pk}}/content/", operation_id=f"{operation}_content")
    @_errors
    def replace_content(request, pk: str, payload: ContentIn, dry_run: bool = False):
        """Replace the regions given; the others are left alone"""
        wire = api.replace_content(
            request,
            name,
            pk,
            payload.content,
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.patch(
        f"{name}/{{pk}}/plugins/{{type_label}}/{{plugin_id}}/",
        operation_id=f"{operation}_plugin_update",
    )
    @_errors
    def update_plugin(
        request,
        pk: str,
        type_label: str,
        plugin_id: str,
        payload: PluginIn,
        dry_run: bool = False,
    ):
        """Change one plugin's fields; needs the object's ETag"""
        wire = api.update_plugin(
            request,
            name,
            pk,
            type_label,
            plugin_id,
            payload.model_dump(),
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.delete(f"{name}/{{pk}}/", operation_id=f"{operation}_delete")
    @_errors
    def delete(request, pk: str, dry_run: bool = False):
        """Delete an object"""
        api.delete(
            request,
            name,
            pk,
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return HttpResponse(status=204)

    @router.delete(f"{name}/{{pk}}/cache/", operation_id=f"{operation}_cache_clear")
    @_errors
    def clear_cache(request, pk: str):
        """Delete the cached rendering, to see changes on the site at once"""
        api.clear_cache(request, name, pk)
        return HttpResponse(status=204)


def _json(wire, status=200):
    response = JsonResponse(wire, status=status)
    response["ETag"] = wire["meta"]["etag"]
    return response


def _errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except APIError as exc:
            return JsonResponse(exc.as_json(), status=exc.status)

    return wrapper
