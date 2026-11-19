"""
Several writes in one transaction: ``POST /batch/``

The operations run in order through ``ContentAPI``'s operations, in one
transaction: all of them are saved, or none. ``{"ref": name}`` stands for
the id of an object created earlier in the batch, wherever an id goes.
"""

from django.db import transaction

from feincms3_api import stored
from feincms3_api.conf import config
from feincms3_api.errors import APIError, ValidationFailedError
from feincms3_api.writing import forget_ids


OPERATIONS = {
    # op: (keys it needs, keys it may have)
    "create": ({"resource"}, {"ref", "fields", "content", "inlines"}),
    "update": ({"resource", "id"}, {"etag", "fields", "content", "inlines"}),
    "replace_content": ({"resource", "id", "content"}, {"etag"}),
    "update_plugin": ({"resource", "id", "plugin", "fields"}, {"etag"}),
    "move": ({"resource", "id", "position"}, {"etag", "relative_to"}),
    "delete": ({"resource", "id"}, {"etag"}),
}


class BatchError(APIError):
    """An operation's error, with the operation's index"""

    def __init__(self, index, exc):
        super().__init__(exc.detail)
        self.status = exc.status
        self.index = index
        self.exc = exc

    def as_json(self):
        data = self.exc.as_json()
        if isinstance(self.exc, ValidationFailedError):
            data["errors"] = [
                error | {"path": _join(f"operations[{self.index}]", error["path"])}
                for error in data["errors"]
            ]
        return {"operation": self.index, **data}


def _join(prefix, path):
    if not prefix or not path:
        return prefix or path
    return f"{prefix}{path}" if path.startswith("[") else f"{prefix}.{path}"


class BatchMixin:
    def batch(self, request, payload, *, dry_run=False):
        """
        Run ``payload["operations"]`` in one transaction; returns each
        operation's result (``None`` for deletions) and the ids of ``refs``
        """
        operations = _check(payload)
        refs, created, written, results = {}, [], set(), []
        with stored.collecting(dry_run=dry_run), transaction.atomic():
            for index, given in enumerate(operations):
                try:
                    operation = _resolve(given, refs, "", mapping=True)
                    result = self._run(request, operation, written, created)
                except APIError as exc:
                    raise BatchError(index, exc) from exc
                if operation["op"] == "create":
                    written.add((operation["resource"], str(result["id"])))
                    if "ref" in operation:
                        refs[operation["ref"]] = result["id"]
                elif operation["op"] != "delete":
                    written.add((operation["resource"], str(operation["id"])))
                results.append(result)
            if dry_run:
                transaction.set_rollback(True)
                for result in results:
                    if result is not None:
                        forget_ids(result, created)
                refs = dict.fromkeys(refs)
        return {"results": results, "refs": refs}

    def _run(self, request, operation, written, created):
        op, name = operation["op"], operation["resource"]
        if op == "create":
            return self.create(
                request, name, _body(operation, "create"), created=created
            )
        pk = operation["id"]
        # What the batch wrote already can't have changed since; an ETag
        # given for it is the one from before the batch
        if_match = "*" if (name, str(pk)) in written else operation.get("etag")
        if op == "update":
            return self.update(
                request,
                name,
                pk,
                _body(operation, "update"),
                if_match=if_match,
                created=created,
            )
        if op == "replace_content":
            return self.replace_content(
                request,
                name,
                pk,
                operation["content"],
                if_match=if_match,
                created=created,
            )
        if op == "update_plugin":
            return self.update_plugin(
                request,
                name,
                pk,
                operation["plugin"]["type"],
                operation["plugin"]["id"],
                {"fields": operation["fields"]},
                if_match=if_match,
            )
        if op == "move":
            return self.move(
                request,
                name,
                pk,
                {
                    key: operation[key]
                    for key in ("position", "relative_to")
                    if key in operation
                },
                if_match=if_match,
                changed=written,
            )
        self.delete(request, name, pk, if_match=if_match)
        return None


def _body(operation, op):
    return {
        key: operation[key]
        for key in ("fields", "content", "inlines")
        if key in operation
    }


def _check(payload):
    """The operations, or every problem with their shape"""
    if not isinstance(payload, dict) or not isinstance(
        operations := payload.get("operations"), list
    ):
        raise ValidationFailedError(
            [{"path": "operations", "message": "Expected a list of operations."}]
        )
    if len(operations) > (limit := config()["MAX_BATCH_OPERATIONS"]):
        raise ValidationFailedError(
            [{"path": "operations", "message": f"At most {limit} operations."}]
        )
    errors, names = [], set()
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, dict):
            errors.append({"path": path, "message": "Expected an object."})
            continue
        if not isinstance(op := operation.get("op"), str) or op not in OPERATIONS:
            errors.append(
                {
                    "path": f"{path}.op",
                    "message": f"One of {', '.join(OPERATIONS)}.",
                }
            )
            continue
        needed, optional = OPERATIONS[op]
        if "resource" in operation and not isinstance(operation["resource"], str):
            errors.append({"path": f"{path}.resource", "message": "Expected a name."})
        errors.extend(
            {"path": f"{path}.{key}", "message": "Missing."}
            for key in sorted(needed - set(operation))
        )
        errors.extend(
            {"path": f"{path}.{key}", "message": "Unknown key."}
            for key in sorted(set(operation) - needed - optional - {"op"})
        )
        if "ref" in operation:
            if not isinstance(ref := operation["ref"], str) or not ref:
                errors.append({"path": f"{path}.ref", "message": "Expected a name."})
            elif ref in names:
                errors.append(
                    {"path": f"{path}.ref", "message": "Used earlier in the batch."}
                )
            else:
                names.add(ref)
        plugin = operation.get("plugin")
        if "plugin" in operation and not (
            isinstance(plugin, dict) and set(plugin) == {"type", "id"}
        ):
            errors.append(
                {
                    "path": f"{path}.plugin",
                    "message": 'Expected {"type": …, "id": …}.',
                }
            )
    if errors:
        raise ValidationFailedError(errors)
    return operations


def _resolve(value, refs, path, *, mapping=False):
    """
    ``value`` with ``{"ref": name}`` replaced by the id of ``name``

    ``mapping``: ``value`` is a mapping of names (an operation, its
    ``fields``), not a value which may be a reference
    """
    if isinstance(value, dict):
        if not mapping and set(value) == {"ref"} and isinstance(value["ref"], str):
            if value["ref"] not in refs:
                raise ValidationFailedError(
                    [
                        {
                            "path": path,
                            "message": f"No object {value['ref']!r} was"
                            " created earlier in the batch.",
                        }
                    ]
                )
            return refs[value["ref"]]
        return {
            # The operation's own ref is a name, not a reference
            key: item
            if key == "ref" and not path
            else _resolve(item, refs, _join(path, key), mapping=key == "fields")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve(item, refs, f"{path}[{i}]") for i, item in enumerate(value)]
    return value
