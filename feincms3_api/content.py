"""
Planning content writes: every check first, then the saves

A content write is planned completely before anything is saved: the
regions, the plugin types allowed in them, the depths, the permissions and
every plugin's form. Problems are collected with their paths, so a write
fails as a whole with all of them listed, or succeeds as a whole.
"""

import dataclasses
import json

from django.contrib.admin.models import LogEntry
from django.utils.translation import gettext as _

from feincms3_api.adapters import form_errors
from feincms3_api.codecs import label
from feincms3_api.resources import allowed_types


STRUCTURAL = ("region", "ordering", "depth")


@dataclasses.dataclass
class PluginSave:
    form: object
    inline: object
    created: bool
    path: str


@dataclasses.dataclass
class ContentPlan:
    saves: list = dataclasses.field(default_factory=list)
    deletes: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)


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


class _Planner:
    """Plans replacing regions; ``plan_content()`` is the entry point"""

    def __init__(self, api, resource, request, obj):
        self.api, self.resource, self.request, self.obj = api, resource, request, obj
        self.existing_obj = obj if obj.pk is not None else None
        inlines = resource.plugin_inlines(request, self.existing_obj)
        self.by_type = {label(inline.model): inline for inline in inlines}
        self.containers = {
            t for t, i in self.by_type.items() if getattr(i.model, "container", False)
        }
        self.region_keys = [region.key for region in resource.regions(obj)]
        self.allowed = allowed_types(inlines, self.region_keys)
        visible, _hidden = resource.plugins(request, inlines, obj)
        self.existing = {(label(type(p)), p.pk): p for p, _inline in visible}
        self.used = set()
        self.contexts = {}
        self.plan = ContentPlan()

    def error(self, path, message):
        self.plan.errors.append({"path": path, "message": message})

    def region(self, key, items, path):
        if key not in self.region_keys:
            regions = ", ".join(self.region_keys) or "none"
            self.error(path, f"Not a region of this object; its regions are {regions}.")
            return
        if not isinstance(items, list):
            self.error(path, "Expected a list.")
            return
        self.plan.errors.extend(check_depths(items, self.containers, path))
        for index, item in enumerate(items):
            self.item(key, index, item, f"{path}[{index}]")

    def item(self, key, index, item, path):
        if not isinstance(item, dict):
            self.error(path, "Expected an object.")
            return
        type_label = item.get("type")
        if (inline := self.by_type.get(type_label)) is None:
            self.error(
                f"{path}.type",
                f"{type_label!r} isn't a plugin type of this object, or not one"
                " you may use.",
            )
            return
        if type_label not in self.allowed[key]:
            self.error(f"{path}.type", f"{type_label} isn't allowed in region {key}.")
            return
        found = self.instance(inline, type_label, item.get("id"), path)
        if found is None:
            return
        plugin, created = found
        if inline not in self.contexts:
            self.contexts[inline] = self.resource.plugin_context(
                self.request, inline, self.existing_obj, self.api
            )
        context = self.contexts[inline]
        structural = {
            "region": key,
            "ordering": (index + 1) * 10,
            "depth": item.get("depth", 0),
        }
        form, errors = self.api.adapters.lookup(type_label).bind(
            context,
            plugin,
            item.get("fields", {}),
            path=f"{path}.fields",
            extra={
                name: value
                for name, value in structural.items()
                if name in context.form_class.base_fields
            },
        )
        self.plan.errors.extend(errors)
        if form is not None:
            self.plan.saves.append(PluginSave(form, inline, created, path))

    def instance(self, inline, type_label, plugin_id, path):
        """``(plugin, created)``: the existing plugin, or a new one"""
        if plugin_id is None:
            if not inline.has_add_permission(self.request, self.existing_obj):
                self.error(path, "No permission to add it.")
                return None
            plugin = inline.model()
            setattr(plugin, inline.fk_name, self.obj)
            return plugin, True
        if (plugin := self.existing.get((type_label, plugin_id))) is None:
            self.error(f"{path}.id", f"No {type_label} with this id on this object.")
            return None
        if (type_label, plugin_id) in self.used:
            self.error(f"{path}.id", "Used twice.")
            return None
        self.used.add((type_label, plugin_id))
        if not inline.has_change_permission(self.request, self.existing_obj):
            self.error(path, "No permission to change it.")
            return None
        return plugin, False

    def deletions(self, content, path):
        """The plugins of the replaced regions which aren't given anymore"""
        for (type_label, plugin_id), plugin in self.existing.items():
            if plugin.region not in content or (type_label, plugin_id) in self.used:
                continue
            if not self.by_type[type_label].has_delete_permission(
                self.request, self.existing_obj
            ):
                self.error(
                    f"{path}.{plugin.region}",
                    f"Leaves out {type_label} {plugin_id}, which you have no"
                    " permission to delete.",
                )
            self.plan.deletes.append(plugin)


def plan_content(api, resource, request, obj, content, *, path="content"):
    """
    Plan replacing the regions in ``content`` of ``obj`` (which may not be
    saved yet). Regions which aren't given are left alone; plugins without
    an inline for the user (no permission) are never touched.
    """
    if not isinstance(content, dict):
        return ContentPlan(errors=[{"path": path, "message": "Expected an object."}])
    planner = _Planner(api, resource, request, obj)
    for key, items in content.items():
        planner.region(key, items, f"{path}.{key}")
    planner.deletions(content, path)
    return planner.plan


def validate_plan(plan):
    """Validate the planned forms; with the plan's own errors, all of them"""
    errors = list(plan.errors)
    for save in plan.saves:
        if not save.form.is_valid():
            errors.extend(form_errors(save.form, f"{save.path}.fields"))
    return errors


def save_plan(plan, obj):
    """Save a validated plan; returns ``(created, log entries)``"""
    created, messages = [], []
    for save in plan.saves:
        plugin = save.form.save(commit=False)
        setattr(plugin, save.inline.fk_name, obj)
        plugin.save()
        save.form.save_m2m()
        name = str(plugin._meta.verbose_name)
        if save.created:
            created.append((label(type(plugin)), plugin.pk))
            messages.append({"added": {"name": name, "object": str(plugin)}})
        elif changed := [
            str(save.form.fields[field].label or field)
            for field in save.form.changed_data
            if field not in STRUCTURAL
        ]:
            messages.append(
                {"changed": {"name": name, "object": str(plugin), "fields": changed}}
            )
    for plugin in plan.deletes:
        messages.append(
            {
                "deleted": {
                    "name": str(plugin._meta.verbose_name),
                    "object": str(plugin),
                }
            }
        )
        plugin.delete()
    return created, messages


def log_message(entries):
    """The admin's change message for ``entries``, marked as an API change"""
    text = LogEntry(change_message=json.dumps(entries)).get_change_message()
    return f"{text or _('No fields changed.')} {_('(Through the API.)')}"
