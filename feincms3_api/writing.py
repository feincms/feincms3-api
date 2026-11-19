"""
Writing: creating, changing and deleting objects, their content and plugins

Every write is one transaction, needs the ETag of what the client read
(``If-Match``), fails as a whole with every problem and its path, can be a
dry run, and is logged in the admin's history.
"""

import os

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils.translation import gettext as _

from feincms3_api import stored
from feincms3_api.adapters import form_errors
from feincms3_api.codecs import label
from feincms3_api.conf import config
from feincms3_api.content import (
    Planner,
    created_objects,
    log_message,
    validate_formsets,
)
from feincms3_api.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    PreconditionRequiredError,
    ValidationFailedError,
)
from feincms3_api.models import Upload


class WritingMixin:
    def upload(self, request, file):
        """Stage a file for a later write, see ``Upload``"""
        if not (request.user.is_active and request.user.is_staff):
            raise ForbiddenError("Only staff users can upload files.")
        name = os.path.basename(file.name)
        upload = Upload(user=request.user, name=name, size=file.size)
        upload.file.save(name, file, save=False)
        upload.save()
        return {
            "upload": str(upload.pk),
            "name": upload.name,
            "size": upload.size,
            "expires_at": (upload.created_at + config()["UPLOAD_LIFETIME"]).isoformat(),
        }

    def _check_etag(self, request, resource, obj, if_match):
        if not if_match:
            raise PreconditionRequiredError(
                "Send If-Match with the ETag of the object as you read it"
                " (or * to overwrite whatever is there)."
            )
        if if_match.strip() == "*":
            return
        current = resource.read(request, obj, self)["meta"]["etag"]
        if current not in {tag.strip() for tag in if_match.split(",")}:
            raise PreconditionFailedError(
                "The object has changed since you read it.", etag=current
            )

    def _check_change(self, request, resource, obj):
        if not resource.model_admin.has_change_permission(request, obj):
            raise ForbiddenError(f"No permission to change this {resource.type}.")

    # ``created``, a list, collects ``(type, id)`` of what a write creates.

    def create(self, request, name, payload, *, dry_run=False, created=None):
        """Create an object, with its content if ``payload`` has some"""
        resource = self.resource(name)
        if not resource.model_admin.has_add_permission(request):
            raise ForbiddenError(f"No permission to add {resource.type}.")
        return self._write(
            request, resource, None, payload, dry_run=dry_run, created=created
        )

    def update(
        self, request, name, pk, payload, *, if_match, dry_run=False, created=None
    ):
        """Change an object's ``fields`` and the regions in its ``content``"""
        resource, obj = self._get(request, name, pk)
        self._check_change(request, resource, obj)
        self._check_etag(request, resource, obj, if_match)
        return self._write(
            request, resource, obj, payload, dry_run=dry_run, created=created
        )

    def replace_content(
        self, request, name, pk, content, *, if_match, dry_run=False, created=None
    ):
        """Replace the regions given in ``content``"""
        return self.update(
            request,
            name,
            pk,
            {"content": content},
            if_match=if_match,
            dry_run=dry_run,
            created=created,
        )

    def _write(
        self,
        request,
        resource,
        obj,
        payload,
        *,
        dry_run,
        plugin=None,
        read=None,
        created=None,
    ):
        """
        Write as the admin's change view does: the object's form, the
        formsets of all its inlines, ``save_form()``, ``save_model()``,
        ``save_related()`` (and so ``save_formset()``) and the change
        message; ``plugin`` is ``(inline, plugin)`` for one plugin's fields
        """
        allowed = {"fields"} if plugin else {"fields", "content", "inlines"}
        if not isinstance(payload, dict):
            raise ValidationFailedError(
                [{"path": "", "message": "Expected an object."}]
            )
        if unknown := set(payload) - allowed:
            raise ValidationFailedError(
                [{"path": key, "message": "Unknown key."} for key in sorted(unknown)]
            )
        adding = obj is None
        with stored.collecting(dry_run=dry_run), transaction.atomic():
            form, target, bound = self._validate(
                request, resource, obj, payload, plugin=plugin
            )
            try:
                self._save(request, resource, form, target, bound, adding=adding)
            finally:
                stored.record(
                    [form, *(each for one in bound for each in one.formset.forms)]
                )
            result = read(target) if read else resource.read(request, target, self)
            new = created_objects(bound)
            if adding:
                new.append((resource.type, target.pk))
            if created is not None:
                created.extend(new)
            if dry_run:
                transaction.set_rollback(True)
                if read is None:
                    forget_ids(result, new)
        return result

    def _validate(self, request, resource, obj, payload, *, plugin):
        """``(form, target, bound formsets)``, or every problem raised"""
        context = resource.context(request, obj, self)
        form, errors = self.adapters.lookup(resource.type).bind(
            context,
            obj,
            {} if plugin else (payload.get("fields") or {}),
            path="fields",
        )
        if form is None:
            raise ValidationFailedError(errors)
        if obj is None and not plugin:
            given = payload.get("fields") or {}
            errors.extend(
                {
                    "path": f"fields.{name}",
                    "message": "Give this field when creating; it has no default here.",
                }
                for name in resource.required_fields(request, self)
                if name not in given
            )
        if form.is_valid():
            errors.extend(resource.validate_instance(form.instance))
            target = resource.model_admin.save_form(
                request, form, change=obj is not None
            )
        else:
            errors.extend(form_errors(form, "fields"))
            # Validating applied the values, e.g. a new page type with other
            # regions for the content below.
            target = form.instance

        planner = Planner(self, resource, request, target)
        if plugin:
            planner.plugin(*plugin, payload.get("fields") or {})
        if payload.get("content") is not None:
            planner.content(payload["content"])
        if payload.get("inlines") is not None:
            planner.other_inlines(payload["inlines"])
        errors.extend(planner.errors)
        bound, bind_errors = planner.bind(target)
        errors.extend(bind_errors)
        errors.extend(validate_formsets(bound))
        if errors:
            raise ValidationFailedError(errors)
        return form, target, bound

    def _save(self, request, resource, form, target, bound, *, adding):
        model_admin = resource.model_admin
        formsets = [one.formset for one in bound]
        try:
            model_admin.save_model(request, target, form, not adding)
            model_admin.save_related(request, form, formsets, not adding)
        except IntegrityError as exc:
            # What the forms and checks didn't find, the database did.
            raise ConflictError(f"The database refused the change: {exc}") from exc
        except ProtectedError as exc:
            raise _protected(exc) from exc
        messages = model_admin.construct_change_message(request, form, formsets, adding)
        if adding:
            model_admin.log_addition(request, target, log_message(messages))
        elif messages:
            model_admin.log_change(request, target, log_message(messages))

    def update_plugin(
        self,
        request,
        name,
        pk,
        type_label,
        plugin_id,
        payload,
        *,
        if_match,
        dry_run=False,
    ):
        """
        Change one plugin's ``fields``; returns the plugin, with the object's
        ``etag`` and ``warnings`` in its ``meta``
        """
        resource, obj = self._get(request, name, pk)
        self._check_change(request, resource, obj)
        found = resource.get_plugin(request, obj, type_label, plugin_id)
        if found is None:
            raise NotFoundError(
                f"No {type_label} with id {plugin_id!r} on this object."
            )
        plugin, inline = found
        if not inline.has_change_permission(request, obj):
            raise ForbiddenError(f"No permission to change {type_label}.")
        self._check_etag(request, resource, obj, if_match)

        def read(target):
            wire = resource.read_plugin(request, target, self, type_label, plugin.pk)
            meta = resource.read(request, target, self)["meta"]
            wire["meta"] = {"etag": meta["etag"], "warnings": meta["warnings"]}
            return wire

        return self._write(
            request,
            resource,
            obj,
            payload,
            dry_run=dry_run,
            plugin=(inline, plugin),
            read=read,
        )

    def move(
        self, request, name, pk, payload, *, if_match, dry_run=False, changed=None
    ):
        """
        Move an object in its tree as the admin's move controls do:
        ``payload`` is ``{"position": …, "relative_to": id}``, the position
        one of the resource's ``move_positions()`` (``relative_to`` isn't
        needed for ``root``). ``changed``, a set, collects ``(name, id)`` of
        the objects whose ETag may have changed: the moved subtree and its
        new siblings.
        """
        resource, obj = self._get(request, name, pk)
        if not resource.move_positions():
            raise ConflictError(f"{resource.type} can't be moved.")
        self._check_change(request, resource, obj)
        errors = []
        if not isinstance(payload, dict):
            errors.append({"path": "", "message": "Expected an object."})
        else:
            errors.extend(
                {"path": key, "message": "Unknown key."}
                for key in sorted(set(payload) - {"position", "relative_to"})
            )
            if not isinstance(payload.get("position"), str):
                errors.append({"path": "position", "message": "Missing."})
        if errors:
            raise ValidationFailedError(errors)
        self._check_etag(request, resource, obj, if_match)
        position, relative_to = payload["position"], payload.get("relative_to")
        with transaction.atomic():
            resource.move(request, obj, position, relative_to)
            _resource, obj = self._get(request, name, pk)
            where = position if relative_to is None else f"{position} {relative_to}"
            resource.model_admin.log_change(
                request, obj, f"Moved ({where}). {_('(Through the API.)')}"
            )
            if changed is not None:
                manager = resource.model._default_manager
                changed.update(
                    (name, str(node))
                    for node in [
                        *manager.filter(parent=obj.parent).values_list("pk", flat=True),
                        *obj.descendants().values_list("pk", flat=True),
                    ]
                )
            result = resource.read(request, obj, self)
            if dry_run:
                transaction.set_rollback(True)
        return result

    def delete(self, request, name, pk, *, if_match, dry_run=False):
        resource, obj = self._get(request, name, pk)
        model_admin = resource.model_admin
        if not model_admin.has_delete_permission(request, obj):
            raise ForbiddenError(f"No permission to delete this {resource.type}.")
        self._check_etag(request, resource, obj, if_match)
        resource.check_delete(request, obj)
        with transaction.atomic():
            model_admin.log_deletions(
                request, resource.model._default_manager.filter(pk=obj.pk)
            )
            try:
                model_admin.delete_model(request, obj)
            except ProtectedError as exc:
                raise _protected(exc) from exc
            if dry_run:
                transaction.set_rollback(True)

    def clear_cache(self, request, name, pk):
        """Delete the cached rendering of an object (``Resource.cache_keys()``)"""
        resource, obj = self._get(request, name, pk)
        self._check_change(request, resource, obj)
        cache.delete_many(resource.cache_keys(request, obj))


def _protected(exc):
    return ConflictError(
        "Other objects refer to it.",
        protected=[
            {"type": label(type(o)), "id": o.pk, "title": str(o)}
            for o in exc.protected_objects
        ],
    )


def forget_ids(wire, created):
    """
    A dry run's result: what was created has no id, it was rolled back;
    references to it in fields neither
    """
    created = set(created)

    def forget(value):
        if isinstance(value, dict):
            if isinstance(value.get("id"), int | str) and (
                (value.get("type"), value["id"]) in created
            ):
                value["id"] = None
            for item in value.values():
                forget(item)
        elif isinstance(value, list):
            for item in value:
                forget(item)

    forget(wire)
    for type_label, items in (wire.get("inlines") or {}).items():
        for item in items:
            if (type_label, item["id"]) in created:
                item["id"] = None
