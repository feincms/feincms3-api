"""The django-ninja router around the operations"""

import datetime as dt
import functools
import re
from typing import Any, Literal

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Body, Field, File, Router, Schema, UploadedFile

from feincms3_api.auth import allowing_read_only
from feincms3_api.errors import APIError, NotFoundError
from feincms3_api.openapi import component_name
from feincms3_api.reading import _bool, expands_references
from feincms3_api.tokens import list_tokens, revoke_current_token, revoke_token


class ObjectIn(Schema):
    fields: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    inlines: dict[str, Any] | None = None


class ContentIn(Schema):
    content: dict[str, Any]


class PluginIn(Schema):
    fields: dict[str, Any]


class APITokenInfo(Schema):
    id: int
    name: str
    prefix: str
    access: Literal["read", "write"]
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime
    current: bool = Field(description="Whether this token authenticated the request.")


def build_router(api):
    router = Router(auth=api.auth, tags=["content"])
    error_type = api.components.annotation("Error")

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

    @router.post(
        "batch/",
        operation_id="batch",
        response={200: api.components.annotation("BatchResult")}
        | dict.fromkeys((403, 404, 409, 412, 422, 428), error_type),
    )
    @_errors
    def batch(
        request,
        payload: Body[api.components.annotation("Batch")],
        dry_run: bool = False,
    ):
        """Several writes in one transaction, all of them or none"""
        return api.batch(request, payload, dry_run=dry_run)

    @router.get("tokens/", operation_id="tokens", response=list[APITokenInfo])
    def tokens(request):
        """The user's tokens which haven't expired, newest first"""
        return list_tokens(request)

    # Read-only tokens may revoke themselves
    revoking = {
        "auth": allowing_read_only(api.auth),
        "response": {200: APITokenInfo} | dict.fromkeys((400, 403, 404), error_type),
    }

    @router.delete("tokens/current/", operation_id="token_revoke_current", **revoking)
    @_errors
    def token_revoke_current(request):
        """Revoke the token authenticating the request (logging out)"""
        return revoke_current_token(request)

    @router.delete("tokens/{token_id}/", operation_id="token_revoke", **revoking)
    @_errors
    def token_revoke(request, token_id: int):
        """Revoke one of the user's tokens; read-only tokens only themselves"""
        return revoke_token(request, token_id)

    for resource in api.resources:
        _add_routes(api, router, resource, api.components)

    # Last, so that it only gets what no route above matched: a JSON answer
    # instead of the site's HTML 404 page
    @router.api_operation(
        ["GET", "POST", "PUT", "PATCH", "DELETE"],
        "{path:rest}",
        include_in_schema=False,
    )
    @_errors
    def not_found(request, rest: str):
        names = [
            resource.name
            for resource in api.resources
            if resource.has_view_permission(request)
        ]
        namespace, _slash, rest_of_path = rest.partition("/")
        name = rest_of_path.split("/", maxsplit=1)[0]
        if namespace == "resources" and name not in names:
            raise NotFoundError(
                f"No resource {name!r}. The resources: {', '.join(names)}."
            )
        if namespace in names:
            raise NotFoundError(
                f"Nothing at {rest!r}; the resource is at 'resources/{namespace}/'."
            )
        if not rest.endswith("/"):
            raise NotFoundError(f"Nothing at {rest!r}; the paths end in a slash.")
        raise NotFoundError(f"Nothing at {rest!r}.")

    return router


def _add_routes(api, router, resource, components):
    name = resource.name
    # The resources have a namespace of their own, so that their names
    # can't collide with the API's own routes
    prefix = f"resources/{name}"
    operation = re.sub(r"[^A-Za-z0-9_]", "_", name)
    component = component_name(resource.type)
    read_type = components.annotation(component)
    list_type = components.annotation(f"{component}List")
    plugin_type = components.annotation(f"{component}Plugin")
    write_type = components.annotation(f"{component}Write", ObjectIn)
    content_write_type = components.annotation(f"{component}ContentWrite", ContentIn)
    plugin_write_type = components.annotation("PluginWrite", PluginIn)
    error_type = components.annotation("Error")

    def responses(ok, *errors):
        return ok | dict.fromkeys((403, 404, *errors), error_type)

    writes = (409, 412, 422, 428)

    if resource.findable:
        # Before {pk}/, which would take "find" for an id
        @router.get(
            f"{prefix}/find/",
            operation_id=f"{operation}_find",
            response=responses({200: read_type}, 409),
        )
        @_errors
        def find(request: HttpRequest):
            """One object by its ``path``"""
            return _json(api.find(request, name, request.GET))

    @router.get(
        f"{prefix}/",
        operation_id=f"{operation}_list",
        response=responses({200: list_type}, 400),
    )
    @_errors
    def list_objects(request: HttpRequest):
        """List: ``limit``, ``offset``, ``q``, filters, ``content``, ``fields``"""
        return api.list(request, name, request.GET)

    @router.get(
        f"{prefix}/{{pk}}/",
        operation_id=f"{operation}_detail",
        response=responses({200: read_type}),
    )
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
        f"{prefix}/{{pk}}/plugins/{{type_label}}/{{plugin_id}}/",
        operation_id=f"{operation}_plugin",
        response=responses({200: plugin_type}),
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

    @router.post(
        f"{prefix}/",
        operation_id=f"{operation}_create",
        response=responses({200: read_type, 201: read_type}, 422),
    )
    @_errors
    def create(request, payload: Body[write_type], dry_run: bool = False):
        """Create an object, with its content if given"""
        wire = api.create(request, name, payload, dry_run=dry_run)
        return _json(wire, 200 if dry_run else 201)

    @router.patch(
        f"{prefix}/{{pk}}/",
        operation_id=f"{operation}_update",
        response=responses({200: read_type}, *writes),
    )
    @_errors
    def update(request, pk: str, payload: Body[write_type], dry_run: bool = False):
        """Change fields, and replace the regions given in ``content``"""
        wire = api.update(
            request,
            name,
            pk,
            payload,
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.put(
        f"{prefix}/{{pk}}/content/",
        operation_id=f"{operation}_content",
        response=responses({200: read_type}, *writes),
    )
    @_errors
    def replace_content(
        request, pk: str, payload: Body[content_write_type], dry_run: bool = False
    ):
        """Replace the regions given; the others are left alone"""
        wire = api.replace_content(
            request,
            name,
            pk,
            payload["content"],
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.patch(
        f"{prefix}/{{pk}}/plugins/{{type_label}}/{{plugin_id}}/",
        operation_id=f"{operation}_plugin_update",
        response=responses({200: plugin_type}, *writes),
    )
    @_errors
    def update_plugin(
        request,
        pk: str,
        type_label: str,
        plugin_id: str,
        payload: Body[plugin_write_type],
        dry_run: bool = False,
    ):
        """Change one plugin's fields; needs the object's ETag"""
        wire = api.update_plugin(
            request,
            name,
            pk,
            type_label,
            plugin_id,
            payload,
            if_match=request.headers.get("If-Match"),
            dry_run=dry_run,
        )
        return _json(wire)

    @router.delete(
        f"{prefix}/{{pk}}/",
        operation_id=f"{operation}_delete",
        response=responses({204: None}, *writes),
    )
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

    @router.delete(
        f"{prefix}/{{pk}}/cache/",
        operation_id=f"{operation}_cache_clear",
        response=responses({204: None}),
    )
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
