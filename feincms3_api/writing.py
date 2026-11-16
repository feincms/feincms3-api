"""
Writing: creating, changing and deleting objects, their content and plugins

Every write is one transaction, needs the ETag of what the client read
(``If-Match``), fails as a whole with every problem and its path, can be a
dry run, and is logged in the admin's history.
"""

import os

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from feincms3_api.adapters import form_errors
from feincms3_api.codecs import label
from feincms3_api.conf import config
from feincms3_api.content import log_message, plan_content, save_plan, validate_plan
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

    def create(self, request, name, payload, *, dry_run=False):
        """Create an object, with its content if ``payload`` has some"""
        resource = self.resource(name)
        if not resource.model_admin.has_add_permission(request):
            raise ForbiddenError(f"No permission to add {resource.type}.")
        return self._write(request, resource, None, payload, dry_run=dry_run)

    def update(self, request, name, pk, payload, *, if_match, dry_run=False):
        """Change an object's ``fields`` and the regions in its ``content``"""
        resource, obj = self._get(request, name, pk)
        self._check_change(request, resource, obj)
        self._check_etag(request, resource, obj, if_match)
        return self._write(request, resource, obj, payload, dry_run=dry_run)

    def replace_content(self, request, name, pk, content, *, if_match, dry_run=False):
        """Replace the regions given in ``content``"""
        return self.update(
            request, name, pk, {"content": content}, if_match=if_match, dry_run=dry_run
        )

    def _write(self, request, resource, obj, payload, *, dry_run):
        if not isinstance(payload, dict):
            raise ValidationFailedError(
                [{"path": "", "message": "Expected an object."}]
            )
        if unknown := set(payload) - {"fields", "content"}:
            raise ValidationFailedError(
                [{"path": key, "message": "Unknown key."} for key in sorted(unknown)]
            )
        model_admin = resource.model_admin
        adding = obj is None
        with transaction.atomic():
            errors, form, target = [], None, obj
            if adding or payload.get("fields") is not None:
                context = resource.context(request, obj, self)
                form, bind_errors = self.adapters.lookup(resource.type).bind(
                    context, obj, payload.get("fields") or {}, path="fields"
                )
                errors.extend(bind_errors)
                if form is not None:
                    if not form.is_valid():
                        errors.extend(form_errors(form, "fields"))
                    else:
                        errors.extend(resource.validate_instance(form.instance))
                    # Validating applied the values, e.g. a new page type
                    # with other regions for the content below.
                    target = form.instance
            plan = None
            if payload.get("content") is not None:
                plan = plan_content(self, resource, request, target, payload["content"])
                errors.extend(validate_plan(plan))
            if errors:
                raise ValidationFailedError(errors)

            try:
                target, messages, created = self._save(
                    request, resource, form, plan, target, adding=adding
                )
            except IntegrityError as exc:
                # What the forms and checks didn't find, the database did.
                raise ConflictError(f"The database refused the change: {exc}") from exc
            if adding:
                model_admin.log_addition(request, target, log_message(messages))
            elif messages:
                model_admin.log_change(request, target, log_message(messages))

            wire = resource.read(request, target, self)
            if dry_run:
                transaction.set_rollback(True)
                _forget_ids(wire, created, adding)
        return wire

    def _save(self, request, resource, form, plan, target, *, adding):
        model_admin = resource.model_admin
        messages, created = [], []
        if form is not None:
            target = form.save(commit=False)
            model_admin.save_model(request, target, form, not adding)
            form.save_m2m()
            messages.extend(
                model_admin.construct_change_message(request, form, None, adding)
            )
        if plan is not None:
            created, plan_messages = save_plan(plan, target)
            messages.extend(plan_messages)
        return target, messages, created

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
        """Change one plugin's ``fields``; returns ``(plugin, object's ETag)``"""
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
        if not isinstance(payload, dict):
            raise ValidationFailedError(
                [{"path": "", "message": "Expected an object."}]
            )

        with transaction.atomic():
            context = resource.plugin_context(request, inline, obj, self)
            form, errors = self.adapters.lookup(type_label).bind(
                context, plugin, payload.get("fields") or {}, path="fields"
            )
            if form is not None and not form.is_valid():
                errors.extend(form_errors(form, "fields"))
            if errors:
                raise ValidationFailedError(errors)
            plugin = form.save()
            if changed := [
                str(form.fields[field].label or field) for field in form.changed_data
            ]:
                resource.model_admin.log_change(
                    request,
                    obj,
                    log_message(
                        [
                            {
                                "changed": {
                                    "name": str(plugin._meta.verbose_name),
                                    "object": str(plugin),
                                    "fields": changed,
                                }
                            }
                        ]
                    ),
                )
            wire = resource.read_plugin(request, obj, self, type_label, plugin.pk)
            etag = resource.read(request, obj, self)["meta"]["etag"]
            if dry_run:
                transaction.set_rollback(True)
        return wire, etag

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
                raise ConflictError(
                    "Other objects refer to it.",
                    protected=[
                        {"type": label(type(o)), "id": o.pk, "title": str(o)}
                        for o in exc.protected_objects
                    ],
                ) from exc
            if dry_run:
                transaction.set_rollback(True)


def _forget_ids(wire, created, adding):
    """A dry run's result: what was created has no id, it was rolled back"""
    if adding:
        wire["id"] = None
    created = set(created)
    for plugins in (wire.get("content") or {}).values():
        for plugin in plugins:
            if (plugin["type"], plugin["id"]) in created:
                plugin["id"] = None
