"""
Planning a write: every check first, then the saves

A write is planned completely before anything is saved: the regions, the
plugin types allowed in them, the depths, the permissions, and what happens
to every row of every inline (plugins and other inlines): it stays, changes,
is added or deleted. Problems are collected with their paths, so a write
fails as a whole with all of them listed, or succeeds as a whole.

The rows are then bound to the admin's formsets (``formsets.py``).
"""

import json

from content_editor.admin import ContentEditorInline
from django.contrib.admin.models import LogEntry
from django.utils.translation import gettext as _

from feincms3_api.codecs import label
from feincms3_api.formsets import Row, bind_formset, formset_errors, prefixes
from feincms3_api.resources import allowed_types, fk_name


def check_depths(items, containers, path):
    """
    The depths of a flat region, checked: the first plugin is at depth 0,
    and a plugin is at most one deeper than the innermost container still
    open before it
    """
    errors = []
    open_containers = []
    for index, item in enumerate(items):
        depth = item.get("depth", 0) if isinstance(item, dict) else 0
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
            errors.append(
                {"path": f"{path}[{index}].depth", "message": "Expected 0 or more."}
            )
            continue
        deepest = open_containers[-1] + 1 if open_containers else 0
        if depth > deepest:
            errors.append(
                {
                    "path": f"{path}[{index}].depth",
                    "message": f"Can be at most {deepest}: a plugin can only be"
                    " one deeper than a container plugin before it.",
                }
            )
            continue
        while open_containers and open_containers[-1] >= depth:
            open_containers.pop()
        if isinstance(item, dict) and item.get("type") in containers:
            open_containers.append(depth)
    return errors


class Planner:
    """
    What a write does to the rows of an object's inlines

    ``obj`` may be new; for an existing object, every row it has starts
    out unchanged. ``content()``, ``inlines()`` and ``plugin()`` change
    them, ``bind()`` binds them to the admin's formsets.
    """

    def __init__(self, api, resource, request, obj):
        self.api, self.resource, self.request, self.obj = api, resource, request, obj
        self.existing_obj = obj if obj.pk is not None else None
        self.errors = []
        self.inlines = resource.model_admin.get_inline_instances(
            request, self.existing_obj
        )
        plugin_inlines = [i for i in self.inlines if _is_plugin_inline(i)]
        self.by_type = {label(inline.model): inline for inline in self.inlines}
        self.containers = {
            label(i.model)
            for i in plugin_inlines
            if getattr(i.model, "container", False)
        }
        self.region_keys = [region.key for region in resource.regions(obj)]
        self.allowed = allowed_types(plugin_inlines, self.region_keys)

        self.rows = {inline: [] for inline in self.inlines}
        self.existing = {}
        visible, _hidden = resource.plugins(request, plugin_inlines, obj)
        positions = {}
        for plugin, inline in visible:
            index = positions.get(plugin.region, 0)
            positions[plugin.region] = index + 1
            self._add_existing(inline, plugin, f"content.{plugin.region}[{index}]")
        for inline in self.inlines:
            if not _is_plugin_inline(inline):
                type_label = label(inline.model)
                for index, item in enumerate(
                    resource.inline_objects(request, inline, self.existing_obj)
                ):
                    self._add_existing(inline, item, f"inlines.{type_label}[{index}]")
        self.used = set()
        self._fields = {}

    def _add_existing(self, inline, instance, path):
        row = Row(instance=instance, path=path)
        self.rows[inline].append(row)
        self.existing[label(type(instance)), instance.pk] = row

    def error(self, path, message):
        self.errors.append({"path": path, "message": message})

    # Content ################################################################

    def content(self, content, path="content"):
        """
        Replace the regions in ``content``. Regions which aren't given are
        left alone; plugins without an inline for the user (no permission)
        are never touched.
        """
        if not isinstance(content, dict):
            self.error(path, "Expected an object.")
            return
        for key, items in content.items():
            self._region(key, items, f"{path}.{key}")
        for (type_label, plugin_id), row in self.existing.items():
            inline = self.by_type[type_label]
            if (
                _is_plugin_inline(inline)
                and row.instance.region in content
                and (type_label, plugin_id) not in self.used
            ):
                self._delete(inline, row, f"{path}.{row.instance.region}")

    def _region(self, key, items, path):
        if key not in self.region_keys:
            regions = ", ".join(self.region_keys) or "none"
            self.error(path, f"Not a region of this object; its regions are {regions}.")
            return
        if not isinstance(items, list):
            self.error(path, "Expected a list.")
            return
        self.errors.extend(check_depths(items, self.containers, path))
        for index, item in enumerate(items):
            item_path = f"{path}[{index}]"
            if not isinstance(item, dict):
                self.error(item_path, "Expected an object.")
                continue
            type_label = item.get("type")
            inline = self.by_type.get(type_label)
            if inline is None or not _is_plugin_inline(inline):
                self.error(
                    f"{item_path}.type",
                    f"{type_label!r} isn't a plugin type of this object, or not"
                    " one you may use.",
                )
                continue
            if type_label not in self.allowed[key]:
                self.error(
                    f"{item_path}.type", f"{type_label} isn't allowed in region {key}."
                )
                continue
            extra = {
                "region": key,
                "ordering": (index + 1) * 10,
                "depth": item.get("depth", 0),
            }
            base_fields = self._base_fields(inline)
            extra = {
                name: value for name, value in extra.items() if name in base_fields
            }
            self._item(inline, item, item_path, extra)

    def _base_fields(self, inline):
        if inline not in self._fields:
            formset_class = inline.get_formset(self.request, self.existing_obj)
            self._fields[inline] = formset_class.form.base_fields
        return self._fields[inline]

    # Other inlines ##########################################################

    def other_inlines(self, inlines, path="inlines"):
        """Replace the objects of the inline types in ``inlines``"""
        if not isinstance(inlines, dict):
            self.error(path, "Expected an object.")
            return
        for type_label, items in inlines.items():
            type_path = f"{path}.{type_label}"
            inline = self.by_type.get(type_label)
            if inline is None or _is_plugin_inline(inline):
                self.error(
                    type_path,
                    f"{type_label!r} isn't an inline of this object, or not one"
                    " you may use.",
                )
                continue
            if not isinstance(items, list):
                self.error(type_path, "Expected a list.")
                continue
            for index, item in enumerate(items):
                item_path = f"{type_path}[{index}]"
                if not isinstance(item, dict):
                    self.error(item_path, "Expected an object.")
                    continue
                if unknown := set(item) - {"id", "fields"}:
                    for key in sorted(unknown):
                        self.error(f"{item_path}.{key}", "Unknown key.")
                    continue
                self._item(inline, {"type": type_label, **item}, item_path, {})
            for row in self.rows[inline]:
                if not row.new and (type_label, row.instance.pk) not in self.used:
                    self._delete(inline, row, type_path)

    # One plugin #############################################################

    def plugin(self, inline, plugin, wire):
        """Change one plugin's ``fields``"""
        row = self.existing[label(type(plugin)), plugin.pk]
        row.wire, row.path = wire, ""

    # Rows ###################################################################

    def _item(self, inline, item, path, extra):
        type_label = item["type"]
        if (plugin_id := item.get("id")) is None:
            if not inline.has_add_permission(self.request, self.existing_obj):
                self.error(path, "No permission to add it.")
                return
            instance = inline.model()
            setattr(instance, fk_name(inline), self.obj)
            self.rows[inline].append(
                Row(instance, path, item.get("fields") or {}, extra)
            )
            return
        if (row := self.existing.get((type_label, plugin_id))) is None:
            self.error(f"{path}.id", f"No {type_label} with this id on this object.")
            return
        if (type_label, plugin_id) in self.used:
            self.error(f"{path}.id", "Used twice.")
            return
        self.used.add((type_label, plugin_id))
        row.path, row.extra = path, extra
        row.wire = item.get("fields") or {}
        changes_something = row.wire or any(
            getattr(row.instance, name, value) != value for name, value in extra.items()
        )
        if changes_something and not inline.has_change_permission(
            self.request, self.existing_obj
        ):
            self.error(path, "No permission to change it.")

    def _delete(self, inline, row, path):
        row.delete = True
        if not (
            inline.can_delete
            and inline.has_delete_permission(self.request, self.existing_obj)
        ):
            self.error(
                path,
                f"Leaves out {label(type(row.instance))} {row.instance.pk}, which"
                " you have no permission to delete.",
            )

    # Formsets ###############################################################

    def bind(self, target):
        """``(bound formsets, errors)``: the rows, bound to the admin's
        formsets of ``target`` (the object as the form made it)"""
        bound, errors = [], []
        formset_classes = [
            inline.get_formset(self.request, self.existing_obj)
            for inline in self.inlines
        ]
        for inline, prefix in zip(self.inlines, prefixes(formset_classes), strict=True):
            context = self.resource.inline_context(
                self.request, inline, self.existing_obj, self.api
            )
            one, bind_errors = bind_formset(
                self.request,
                inline,
                target,
                self.rows[inline],
                context,
                self.api.adapters.lookup(context.type),
                prefix=prefix,
                path="content"
                if _is_plugin_inline(inline)
                else f"inlines.{context.type}",
            )
            bound.append(one)
            errors.extend(bind_errors)
        return bound, errors


def _is_plugin_inline(inline):
    return isinstance(inline, ContentEditorInline)


def validate_formsets(bound):
    errors = []
    for one in bound:
        if not one.formset.is_valid():
            errors.extend(formset_errors(one))
    return errors


def created_objects(bound):
    """``(type, id)`` of the objects the saved formsets added"""
    return [
        (label(type(obj)), obj.pk) for one in bound for obj in one.formset.new_objects
    ]


def log_message(entries):
    """The admin's change message for ``entries``, marked as an API change"""
    text = LogEntry(change_message=json.dumps(entries)).get_change_message()
    return f"{text or _('No fields changed.')} {_('(Through the API.)')}"
