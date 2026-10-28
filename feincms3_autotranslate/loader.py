"""
Loading translated objects as inactive copies

Django's deserialization does the reading; what ``loaddata`` would do next,
saving with ``raw=True`` under the original primary keys, is left out.
Instead every object gets a fresh primary key, its references are mapped,
its own fields rewritten, and it goes through ``full_clean()`` and
``save()``. Nothing but inserts, and replacing content with ``--replace``.

The same decisions answer ``--plan``, which only needs the dumps and the
target database, and nothing is written then.
"""

import dataclasses
import json
from functools import cached_property

from django.apps import apps
from django.core import serializers
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify

from feincms3_autotranslate.conf import first_language
from feincms3_autotranslate.datasets import (
    _describe,
    has_field,
    has_unique_slug,
    label,
    plugin_models,
    registry as default_registry,
    translated_fields,
)
from feincms3_autotranslate.fields import (
    HTML,
    LINK,
    field_specs,
    iter_path,
    json_proxies,
)
from feincms3_autotranslate.links import (
    host_of,
    map_html,
    page_location,
    path_scope,
    split_url,
)
from feincms3_autotranslate.strings import (
    OWNER,
    REGIONS,
    DumpError,
    all_objects,
    group_units,
    source_language,
    unit_key,
    unit_strings,
)


CREATE = "create"
REPLACE = "replace"
SKIP = "skip"
FAIL = "fail"

LOG_STATUS = {CREATE: "created", REPLACE: "replaced", SKIP: "skipped", FAIL: "failed"}


class Failed(Exception):
    pass


def admin_url(obj):
    try:
        return reverse(
            f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
            args=[obj.pk],
        )
    except NoReverseMatch:
        return ""


def location(obj):
    """Path or slug, whatever says best where an object is"""
    if path := getattr(obj, "path", None):
        return path
    return getattr(obj, "slug", None) or ""


def _proxy_class(obj):
    """
    The proxy class of a typed plugin (django-json-schema-editor,
    feincms3-forms), whose ``save()`` needs its ``TYPE``
    """
    model = type(obj)
    if not has_field(model, "type"):
        return model
    for proxy in json_proxies(model):
        if proxy.TYPE == obj.type:
            return proxy
    stack = list(model.__subclasses__())
    while stack:
        sub = stack.pop()
        if sub._meta.proxy and sub.__dict__.get("TYPE") == obj.type:
            return sub
        stack.extend(sub.__subclasses__())
    return model


def _reset_pk(obj):
    for parent, link in obj._meta.parents.items():
        setattr(obj, parent._meta.pk.attname, None)
        if link:
            setattr(obj, link.attname, None)
    obj.pk = None
    obj._state.adding = True


def _foreign_keys(obj):
    return [
        field
        for field in obj._meta.concrete_fields
        if field.is_relation and (field.many_to_one or field.one_to_one)
    ]


def _is_slug_clash(exc):
    if not hasattr(exc, "error_dict"):
        return False
    for name in ("slug", "path"):
        if any(error.code == "unique" for error in exc.error_dict.get(name, ())):
            return True
    return any(
        error.code in {"unique", "unique_together"}
        and "slug" in (error.params or {}).get("unique_check", ())
        for error in exc.error_dict.get(NON_FIELD_ERRORS, ())
    )


def _message(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "error_dict"):
            return "; ".join(
                f"{field}: {' '.join(messages)}"
                for field, messages in exc.message_dict.items()
            )
        return " ".join(exc.messages)
    return str(exc)


@dataclasses.dataclass(eq=False)
class Entry:
    unit: object
    model: type
    config: object
    deserialized: list
    status: str | None = None
    reason: str = ""
    action: str = ""
    original: object = None
    existing: object = None
    # The object receiving the content: the copy, the existing translation,
    # or an object with a region per language itself.
    target: object = None
    # Set for pages whose parent isn't part of the run.
    parent: object = None
    notes: list = dataclasses.field(default_factory=list)
    saved: list = dataclasses.field(default_factory=list)
    deferred: list = dataclasses.field(default_factory=list)
    child_copies: dict = dataclasses.field(default_factory=dict)
    done: bool = False

    @property
    def key(self):
        return self.unit.key

    @property
    def is_regions(self):
        return self.unit.kind == REGIONS

    def fail(self, reason):
        self.action = FAIL
        self.reason = reason


class Run:
    def __init__(
        self,
        objects,
        *,
        target,
        statuses=None,
        replace=False,
        activate=False,
        into=None,
        parent=None,
        registry=None,
    ):
        self.registry = registry or default_registry()
        try:
            self.units = group_units(objects, self.registry)
            self.source = source_language(self.units)
        except DumpError as exc:
            raise CommandError(str(exc)) from exc
        self.target = target
        if self.target == self.source:
            raise CommandError(
                f"The target language is the source language ({self.source})."
                f" Copying without translating is out of scope."
            )
        self.statuses = statuses
        self.dry_run = False
        self.replace = replace
        self.activate = activate
        self.into = into or {}
        self.parent_pk = parent
        self.problems = {
            "references": [],
            "regions": [],
            "translated_fields": [],
            "live": [],
            "static_paths": [],
        }
        self._translation_cache = {}
        self._object_cache = {}

        deserialized = list(
            serializers.deserialize("json", json.dumps(objects), ignorenonexistent=True)
        )
        by_key = {
            unit_key(raw["model"], raw["pk"]): ds
            for raw, ds in zip(objects, deserialized)
        }
        self.entries = {}
        # (label, pk) of a plugin or related object -> its entry
        self.child_entries = {}
        for unit in self.units.values():
            config = self.registry.translatable.get(
                unit.label
            ) or self.registry.language_regions.get(unit.label)
            entry = Entry(
                unit=unit,
                model=apps.get_model(unit.label),
                config=config,
                deserialized=[
                    by_key[unit_key(r["model"], r["pk"])] for r in unit.objects()
                ],
            )
            if statuses is not None:
                state = statuses.get(unit.key, {})
                entry.status = state.get("status", "untranslated")
                entry.reason = state.get("reason", "")
            self.entries[unit.key] = entry
            for raw in unit.children:
                self.child_entries[(raw["model"], raw["pk"])] = entry

    # Lookups ################################################################

    def _get(self, model, pk):
        key = (label(model), pk)
        if key not in self._object_cache:
            self._object_cache[key] = model._base_manager.filter(pk=pk).first()
        return self._object_cache[key]

    def _primary_pk(self, obj):
        """The first-language object ``translation_of`` points at"""
        if obj.language_code == first_language():
            return obj.pk
        return obj.translation_of_id

    def translation(self, model, obj):
        """The existing translation of ``obj`` in the target language"""
        key = (label(model), obj.pk)
        if key in self._translation_cache:
            return self._translation_cache[key]
        result = None
        if has_field(model, "translation_of") and has_field(model, "language_code"):
            primary = self._primary_pk(obj)
            if primary is None:
                result = None
            elif self.target == first_language():
                result = (
                    model._default_manager.filter(pk=primary).first()
                    if primary != obj.pk
                    else None
                )
            else:
                result = model._default_manager.filter(
                    language_code=self.target, translation_of=primary
                ).first()
        if (
            result is None
            and self._is_tree(model)
            and getattr(obj, "parent_id", True) is None
            and getattr(obj, "language_code", None) == self.source
        ):
            result = self._target_root(model, obj)
        self._translation_cache[key] = result
        return result

    def _is_tree(self, model):
        config = self.registry.translatable.get(label(model))
        return bool(config and config.tree)

    def _scope(self, model, obj):
        return {
            field: getattr(obj, field)
            for field in path_scope(model)
            if field != "language_code"
        }

    def _target_root(self, model, source_root):
        roots = list(
            model._default_manager.filter(
                parent=None,
                language_code=self.target,
                **self._scope(model, source_root),
            )[:2]
        )
        return roots[0] if len(roots) == 1 else None

    # Decisions ##############################################################

    def decide(self):
        """Decide what happens to each unit; raise if the run can't go on"""
        stops = []
        for entry in self.entries.values():
            if entry.is_regions:
                self._decide_regions(entry)
            else:
                self._decide_object(entry)

        for entry in self.entries.values():
            if entry.action == CREATE and entry.config.tree:
                try:
                    self._decide_parent(entry)
                except Failed as exc:
                    stops.append(str(exc))
        if stops:
            raise CommandError("\n".join(stops))

        if self.statuses is not None:
            for entry in self.entries.values():
                if entry.action not in {CREATE, REPLACE}:
                    continue
                if entry.status == "failed":
                    entry.fail(f"Translating failed: {entry.reason}")
                elif entry.status != "translated":
                    entry.fail(
                        "It wasn't translated, because the plan skipped it."
                        " Run the same selection again to translate it."
                    )
        self._propagate_failures()

    def _propagate_failures(self):
        changed = True
        while changed:
            changed = False
            for entry in self.entries.values():
                if entry.action != CREATE or not entry.config.tree:
                    continue
                parent = self.entries.get(
                    unit_key(entry.unit.label, entry.original.parent_id)
                )
                if parent and parent.action == FAIL:
                    entry.fail(f"Its parent {parent.original} failed.")
                    changed = True

    def _decide_regions(self, entry):
        obj = self._get(entry.model, entry.unit.pk)
        entry.original = obj
        if obj is None:
            entry.fail("It doesn't exist in this database.")
            return
        keys = {region.key for region in obj.regions}
        if missing := {self.source, self.target} - keys:
            entry.fail(f"It has no region for {', '.join(sorted(missing))}.")
            return
        entry.target = obj
        if self._has_target_region(obj):
            if self.replace:
                entry.action = REPLACE
            else:
                entry.action = SKIP
                entry.reason = f"Its {self.target} region has plugins already."
        else:
            entry.action = CREATE

    def _has_target_region(self, obj):
        return any(
            plugin._default_manager.filter(parent=obj, region=self.target).exists()
            for plugin in plugin_models(type(obj))
        )

    def _decide_object(self, entry):
        model = entry.model
        original = self._get(model, entry.unit.pk)
        entry.original = original
        if original is None:
            entry.fail(
                "The original doesn't exist in this database; was the dump"
                " made from a different one?"
            )
            return

        if (into := self.into.get((label(model), original.pk))) is not None:
            existing = self._get(model, into)
            if existing is None:
                entry.fail(f"--into names {label(model)} {into}, which doesn't exist.")
                return
        else:
            existing = self.translation(model, original)
            if existing is not None and existing.pk == original.pk:
                existing = None

        if existing is not None:
            entry.existing = existing
            if self.replace:
                entry.action = REPLACE
                entry.target = existing
            else:
                entry.action = SKIP
                entry.reason = f"Translated already: {_describe(existing)}."
            return

        if clash := self._application_clash(original):
            entry.action = SKIP
            entry.reason = (
                f"The target language has an application page of this type"
                f" already: {_describe(clash)}."
            )
            return
        entry.action = CREATE

    def _application_clash(self, page):
        try:
            from feincms3.applications import ApplicationType  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            return None
        type = getattr(page, "type", None)
        if not isinstance(type, ApplicationType) or not has_field(
            page.__class__, "app_namespace"
        ):
            return None
        return (
            page
            ._clash_candidates()
            .filter(
                app_namespace=type.app_namespace(page),
                language_code=self.target,
            )
            .first()
        )

    def _decide_parent(self, entry):
        original = entry.original
        model = entry.model
        if unit_key(entry.unit.label, original.parent_id) in self.entries:
            return
        if self.parent_pk is not None:
            entry.parent = self._get(model, self.parent_pk)
            if entry.parent is None:
                raise Failed(f"--parent {self.parent_pk} doesn't exist.")
            return
        parent = self._get(model, original.parent_id)
        if counterpart := self.translation(model, parent):
            entry.parent = counterpart
            return
        raise Failed(
            f"{_describe(original)} needs a {self.target} counterpart of its"
            f" parent {_describe(parent)}. Translate that page first, or pass"
            f" --parent <pk>."
        )

    # Plan ###################################################################

    def plan(self):
        self.decide()
        objects = {}
        chars = 0
        for entry in self.entries.values():
            item = {"action": entry.action}
            if entry.reason:
                item["reason"] = entry.reason
            if entry.action in {CREATE, REPLACE}:
                strings, _html = unit_strings(entry.unit)
                item["characters"] = sum(len(text) for text in strings.values())
                chars += item["characters"]
            objects[entry.key] = item
        plan = {
            "feincms3_autotranslate_plan": 1,
            "source": self.source,
            "target": self.target,
            "replace": self.replace,
            "objects": objects,
        }
        count = dict.fromkeys((CREATE, REPLACE, SKIP, FAIL), 0)
        for item in objects.values():
            count[item["action"]] += 1
        summary = (
            f"To translate: {count[CREATE] + count[REPLACE]} objects"
            f" ({count[CREATE]} to create, {count[REPLACE]} to replace),"
            f" {chars} characters. Skipped: {count[SKIP]}. Failed: {count[FAIL]}."
        )
        return plan, summary

    # Loading ################################################################

    def load(self, *, dry_run=False):
        self.decide()
        order = self._order()
        self.dry_run = dry_run
        with transaction.atomic():
            for entry in order:
                if entry.action not in {CREATE, REPLACE}:
                    continue
                try:
                    with transaction.atomic():
                        self._save(entry)
                except (Failed, ValidationError, DatabaseError) as exc:
                    entry.fail(_message(exc))
                    entry.target = entry.original if entry.is_regions else None
                    entry.saved = []
                    entry.deferred = []
                    entry.child_copies = {}
                    self._propagate_failures()
                else:
                    entry.done = True
            self._fill_deferred()
            self._map_text_references()
            self._collect_problems()
            log = self._log()
            if dry_run:
                transaction.set_rollback(True)
        return log

    def _order(self):
        """Units in an order where everything a unit needs exists before it"""
        deps = {}
        for entry in self.entries.values():
            deps[entry] = set()
            if entry.action not in {CREATE, REPLACE}:
                continue
            for ds in entry.deserialized:
                is_owner = ds is entry.deserialized[0]
                for field in _foreign_keys(ds.object):
                    value = getattr(ds.object, field.attname)
                    if value is None or field.name == "translation_of":
                        continue
                    hard = (
                        not field.null
                        or field.remote_field.parent_link
                        or (is_owner and entry.config.tree and field.name == "parent")
                    )
                    if not hard:
                        continue
                    related = field.related_model._meta.concrete_model
                    dep = self.entries.get(unit_key(label(related), value))
                    dep = dep or self.child_entries.get((label(related), value))
                    if dep is not None and dep is not entry and dep.action == CREATE:
                        deps[entry].add(dep)

        order, state = [], {}

        def visit(entry, path):
            if state.get(entry) == "done":
                return
            if state.get(entry) == "visiting":
                cycle = " -> ".join(e.key for e in [*path, entry])
                raise CommandError(
                    f"These objects need each other to exist first: {cycle}."
                    f" Nothing has been loaded."
                )
            state[entry] = "visiting"
            for dep in sorted(deps[entry], key=lambda e: e.key):
                visit(dep, [*path, entry])
            state[entry] = "done"
            order.append(entry)

        for entry in self.entries.values():
            visit(entry, [])
        return order

    def counterpart(self, model, pk):
        """
        ``(pk, state)`` of what a reference to ``model`` ``pk`` becomes

        ``state`` is ``"copy"`` (an object of this run), ``"translation"``
        (an existing translation), ``"pending"`` (an object of this run which
        isn't saved yet) or ``"same"`` (the reference stays).
        """
        model = model._meta.concrete_model
        key = unit_key(label(model), pk)
        if entry := self.entries.get(key):
            if entry.is_regions:
                return pk, "self"
            if entry.target is not None and entry.target.pk is not None:
                return entry.target.pk, "copy"
            if entry.action == CREATE and not entry.done:
                return None, "pending"
        elif entry := self.child_entries.get((label(model), pk)):
            if (new := entry.child_copies.get((label(model), pk))) is not None:
                return new, "copy"
            if entry.action in {CREATE, REPLACE} and not entry.done:
                return None, "pending"
            return pk, "same"
        if (obj := self._get(model, pk)) and (
            translation := self.translation(model, obj)
        ):
            return translation.pk, "translation"
        return pk, "same"

    def _map_foreign_keys(self, entry, obj, *, skip=()):
        """Map the references of ``obj``, return the names of those changed"""
        changed = set()
        for field in _foreign_keys(obj):
            if field.name in skip or field.name == "translation_of":
                continue
            if field.remote_field.parent_link:
                continue
            value = getattr(obj, field.attname)
            if value is None:
                continue
            new, state = self.counterpart(field.related_model, value)
            if state == "pending":
                if field.null:
                    setattr(obj, field.attname, None)
                    entry.deferred.append((obj, field, value))
                    changed.add(field.name)
                else:
                    self._reference_left(
                        entry, obj, field.name, field.related_model, value
                    )
            elif state == "same":
                self._reference_left(entry, obj, field.name, field.related_model, value)
            else:
                setattr(obj, field.attname, new)
                if new != value:
                    changed.add(field.name)
        return changed

    def _reference_left(self, entry, obj, field_name, model, pk):
        model = model._meta.concrete_model
        target = self._get(model, pk)
        if target is None:
            return
        # The text is put together in report(), once the copy is saved
        where = (entry, f"{label(type(obj))}.{field_name}")
        language = getattr(target, "language_code", None)
        if language and language != self.target:
            self.problems["references"].append((*where, _describe(target)))
        if label(
            model
        ) in self.registry.language_regions and not self._has_target_region(target):
            self.problems["regions"].append((
                *where,
                f"{_describe(target)}, which has nothing in its {self.target} region",
            ))
        for name, columns in translated_fields(model).items():
            source_col = columns.get(self.source)
            target_col = columns.get(self.target)
            if (
                source_col
                and target_col
                and getattr(target, source_col)
                and not getattr(target, target_col)
            ):
                self.problems["translated_fields"].append((
                    *where,
                    f"{_describe(target)}: {name} has no {self.target} text",
                ))

    def _save(self, entry):
        owner_ds, *children = entry.deserialized
        if entry.is_regions:
            if entry.action == REPLACE:
                for plugin in plugin_models(entry.model):
                    plugin._default_manager.filter(
                        parent=entry.target, region=self.target
                    ).delete()
        elif entry.action == CREATE:
            self._create_owner(entry, owner_ds)
        else:
            self._replace_owner(entry, owner_ds)

        for ds in children:
            obj = ds.object
            old_pk = obj.pk
            _reset_pk(obj)
            if entry.is_regions:
                obj.region = self.target
            changed = self._map_foreign_keys(entry, obj)
            obj.__class__ = _proxy_class(obj)
            # Only what the run wrote is validated: a value copied as it is
            # can't be fixed here, and the original has it already.
            changed |= {
                spec.field
                for spec in field_specs(type(obj))
                if getattr(obj, spec.field) not in ("", None)
            }
            if entry.is_regions:
                changed.add("region")
            obj.full_clean(
                exclude=[f.name for f in obj._meta.fields if f.name not in changed]
            )
            obj.save()
            entry.child_copies[(label(obj._meta.concrete_model), old_pk)] = obj.pk
            entry.saved.append(ds)

    def _rewrite_owner(self, entry, obj):
        original = entry.original
        model = entry.model
        obj.language_code = self.target
        if has_field(model, "is_active"):
            obj.is_active = self.activate
        for name, value in (entry.config.offline or {}).items():
            setattr(obj, name, value)
        if has_field(model, "translation_of"):
            primary = self._primary_pk(original)
            obj.translation_of_id = None if self.target == first_language() else primary
        if entry.config.tree:
            parent = entry.parent
            if parent is None:
                parent_entry = self.entries[unit_key(label(model), original.parent_id)]
                parent = parent_entry.target or parent_entry.existing
            if parent is None or parent.pk is None:
                raise Failed("Its parent has no counterpart.")
            obj.parent_id = parent.pk
            # The parent instance has to be current for path computation.
            obj.parent = model._default_manager.get(pk=parent.pk)
            if getattr(obj, "static_path", False):
                obj.static_path = False
                self.problems["static_paths"].append(original)

    def _create_owner(self, entry, ds):
        obj = ds.object
        _reset_pk(obj)
        self._rewrite_owner(entry, obj)
        entry.target = obj
        self._map_foreign_keys(entry, obj, skip={"parent"} if entry.config.tree else ())
        self._clean_with_slug(entry, obj)
        obj.save()
        entry.saved.append(ds)

    def _clean_with_slug(self, entry, obj):
        model = entry.model
        base = None
        if has_field(model, "slug") and (
            title := getattr(obj, entry.config.title_field, "")
        ):
            base = slugify(title)
            max_length = model._meta.get_field("slug").max_length
            if max_length:
                base = base[:max_length].strip("-")
        if not base:
            obj.full_clean()
            return
        for number in range(1, 101):
            suffix = "" if number == 1 else f"-{number}"
            obj.slug = f"{base[: (max_length or len(base) + len(suffix)) - len(suffix)]}{suffix}"
            try:
                obj.full_clean()
            except ValidationError as exc:
                if not _is_slug_clash(exc):
                    raise
                if not has_field(model, "translation_of"):
                    raise Failed(
                        f"The slug {obj.slug!r} is taken. Without translation_of"
                        f" that's the only hint of an earlier copy; delete it, or"
                        f" name it with --into {label(model)}:{entry.original.pk}=<pk>."
                    ) from exc
                continue
            if number > 1:
                entry.notes.append(f"The slug {base!r} was taken, it's {obj.slug!r}.")
            return
        raise Failed(f"No free slug for {base!r}.")

    def _replace_owner(self, entry, ds):
        translated = ds.object
        existing = entry.existing
        names = {spec.field for spec in field_specs(entry.model)}
        # Only the content: references, slug, path, parent, position, menu
        # and is_active are what an editor decided about the copy.
        for name in names:
            setattr(existing, name, getattr(translated, name))
        existing.full_clean()
        existing.save()
        for plugin in plugin_models(entry.model):
            plugin._default_manager.filter(parent=existing).delete()
        for rel_model, name in entry.config.related:
            rel_model._default_manager.filter(**{name: existing}).delete()
        # The translated object stands in for the existing one from now on,
        # so that the text references of its fields are mapped below.
        entry.saved.append(_Saved(existing, ds.m2m_data))

    def _fill_deferred(self):
        for entry in self.entries.values():
            for obj, field, value in entry.deferred:
                new, state = self.counterpart(field.related_model, value)
                if state in {"copy", "translation"}:
                    type(obj)._base_manager.filter(pk=obj.pk).update(**{
                        field.attname: new
                    })
                else:
                    self._reference_left(
                        entry, obj, field.name, field.related_model, value
                    )
            for ds in entry.saved:
                for name, pks in (ds.m2m_data or {}).items():
                    field = ds.object._meta.get_field(name)
                    mapped = []
                    for pk in pks:
                        new, state = self.counterpart(field.related_model, pk)
                        if state in {"copy", "translation", "self"}:
                            mapped.append(new)
                        else:
                            self._reference_left(
                                entry, ds.object, name, field.related_model, pk
                            )
                            mapped.append(pk)
                    getattr(ds.object, name).set(mapped)

    # Text references ########################################################

    @cached_property
    def _flat_urls(self):
        """Paths of the source-language objects without a tree"""
        result = {}
        for config in self.registry.translatable.values():
            if config.tree or not hasattr(config.model, "get_absolute_url"):
                continue
            for obj in config.model._default_manager.filter(language_code=self.source):
                if parts := split_url(_absolute_url(obj)):
                    result.setdefault(parts[1], (config.model, obj.pk))
        return result

    def _resolve_path(self, path, context):
        for config in self.registry.translatable.values():
            if not config.tree:
                continue
            model = config.model
            scope = {
                field: getattr(context, field)
                for field in path_scope(model)
                if hasattr(context, field)
            }
            candidates = {path, f"{path}/"} if not path.endswith("/") else {path}
            if page := model._default_manager.filter(
                path__in=candidates, **scope
            ).first():
                return model, page.pk
        return self._flat_urls.get(path)

    def _public_url(self, obj):
        """The URL of an object once it's active"""
        if self._is_tree(type(obj)):
            return page_location(obj)
        restore = getattr(obj, "is_active", None)
        if restore is not None:
            obj.is_active = True
        try:
            url = obj.get_absolute_url()
        except Exception:  # noqa: BLE001
            return None
        finally:
            if restore is not None:
                obj.is_active = restore
        parts = split_url(url) if url else None
        if not parts or parts[1] in {"", "/"} or parts[1].startswith(reverse_admin()):
            return None
        return host_of(parts[0]), parts[1]

    def _map_url(self, entry, obj, field_name, url):
        parts = split_url(url)
        if not parts:
            return url
        origin, path, rest = parts
        resolved = self._resolve_path(path, entry.original)
        if resolved is None:
            return url
        model, pk = resolved
        new, state = self.counterpart(model, pk)
        if state not in {"copy", "translation"}:
            self._reference_left(entry, obj, field_name, model, pk)
            return url
        location = self._public_url(
            self._get(model, new) or model._base_manager.get(pk=new)
        )
        if location is None:
            self._reference_left(entry, obj, field_name, model, pk)
            return url
        host, new_path = location
        own_host = (
            (self._public_url(entry.target) or (None, None))[0]
            if entry.target
            else None
        )
        if host and host != own_host:
            return f"https://{host}{new_path}{rest}"
        if host is None and origin:
            return f"{origin}{new_path}{rest}"
        return f"{new_path}{rest}"

    def _map_text_references(self):
        for entry in self.entries.values():
            if not entry.done:
                continue
            for ds in entry.saved:
                obj = ds.object
                self._object_cache.pop((label(obj._meta.concrete_model), obj.pk), None)
                specs = field_specs(type(obj))
                fields = {spec.field: getattr(obj, spec.field) for spec in specs}
                changed = set()
                for spec in specs:
                    if spec.kind not in {HTML, LINK}:
                        continue
                    for _concrete, container, key in iter_path(fields, spec.path):
                        value = container[key]
                        if not isinstance(value, str) or not value:
                            continue
                        new = (
                            self._map_html(entry, obj, spec.field, value)
                            if spec.kind == HTML
                            else self._map_url(entry, obj, spec.field, value)
                        )
                        if new != value:
                            container[key] = new
                            changed.add(spec.field)
                changed |= self._map_json_foreign_keys(entry, obj, fields)
                if changed:
                    for name in changed:
                        setattr(obj, name, fields[name])
                    type(obj)._base_manager.filter(pk=obj.pk).update(**{
                        name: fields[name] for name in changed
                    })

    def _map_html(self, entry, obj, field_name, text):
        return map_html(text, lambda url: self._map_url(entry, obj, field_name, url))

    def _map_json_foreign_keys(self, entry, obj, fields):
        paths_by_model = getattr(type(obj), "_proxy_types_foreign_key_paths", {}).get(
            getattr(obj, "type", None), {}
        )
        if not paths_by_model:
            return set()
        fields.setdefault("data", obj.data)
        changed = set()
        for model_label, paths in paths_by_model.items():
            model = apps.get_model(model_label)
            for path in paths:
                for _concrete, container, key in iter_path(
                    {"data": fields["data"]}, f"data.{_jmes_to_path(path)}"
                ):
                    value = container[key]
                    if value in (None, ""):
                        continue
                    new, state = self.counterpart(model, value)
                    if state in {"copy", "translation"} and new != value:
                        container[key] = (
                            type(value)(new) if isinstance(value, str) else new
                        )
                        changed.add("data")
                    elif state == "same":
                        self._reference_left(entry, obj, f"data.{path}", model, value)
        return changed

    # Reporting ##############################################################

    def _collect_problems(self):
        for entry in self.entries.values():
            if not entry.is_regions or entry.action not in {CREATE, REPLACE}:
                continue
            if not entry.done:
                continue
            for config in self.registry.translatable.values():
                for plugin in plugin_models(config.model):
                    for field in _foreign_keys(plugin._meta.model):
                        if field.related_model is not entry.model:
                            continue
                        filters = {field.name: entry.target}
                        filters["parent__language_code"] = self.target
                        if has_field(config.model, "is_active"):
                            filters["parent__is_active"] = True
                        for p in plugin._default_manager.filter(
                            **filters
                        ).select_related("parent"):
                            self.problems["live"].append(
                                f"{_describe(entry.target)} is live on {_describe(p.parent)}"
                            )

    def _log(self):
        objects = []
        for entry in self.entries.values():
            item = {
                "model": label(entry.model),
                "original": entry.unit.pk,
                "status": LOG_STATUS[entry.action],
            }
            if entry.is_regions:
                item["region"] = self.target
            target = (
                entry.target if entry.action in {CREATE, REPLACE} else entry.existing
            )
            if target is not None and target.pk is not None and not self.dry_run:
                item["copy"] = target.pk
            if target is not None and target.pk is not None:
                item["location"] = location(target)
                if not self.dry_run or entry.action == SKIP:
                    item["admin_url"] = admin_url(target)
            if entry.reason:
                item["reason"] = entry.reason
            objects.append(item)
        return {
            "feincms3_autotranslate_log": 1,
            "dry_run": self.dry_run,
            "activate": self.activate,
            "source": self.source,
            "target": self.target,
            "objects": objects,
            # Shared objects with a region per language, live on loading
            "live": sorted(set(self.problems["live"])),
        }

    def report(self):
        lines = []

        def section(title, items):
            if items:
                lines.append(f"\n{title}")
                lines.extend(f"  {item}" for item in items)

        section(
            "GOES LIVE AT ONCE (shared objects with a region per language):",
            sorted(set(self.problems["live"])),
        )
        warnings = []
        if self.activate and any(
            e.action == CREATE and has_field(e.model, "is_active")
            for e in self.entries.values()
        ):
            warnings.append(
                "--activate: the new copies are active and visible as soon as"
                " they're loaded. Translations which existed already keep their"
                " is_active, and offline={...} of the datasets still applies."
            )
        for config in self.registry.translatable.values():
            model = config.model
            if not any(e.model is model for e in self.entries.values()):
                continue
            if not has_field(model, "translation_of"):
                warnings.append(
                    f"{label(model)} has no translation_of: earlier copies can't"
                    f" be recognised, running again creates another copy."
                    + (
                        ""
                        if has_unique_slug(model)
                        else " It has no unique slug either, so not even a clash"
                        " would show a duplicate."
                    )
                )
        if self.dry_run:
            warnings.append(
                "Signal handlers ran in this dry run too; only the database"
                " writes have been rolled back."
            )
        section("Warnings:", warnings)

        def describe(entry):
            target = entry.target or entry.existing
            text = f"{label(entry.model)} {entry.original or entry.unit.pk}"
            if entry.original is not None and (where := location(entry.original)):
                text += f" {where}"
            if target is not None and target.pk is not None and not entry.is_regions:
                text += f" -> {location(target)}"
                # The copies of a dry run are gone, and their pks with them
                if not self.dry_run or entry.action == SKIP:
                    text += f" {admin_url(target)}"
            if entry.is_regions:
                text += f" (region {self.target})"
            return text

        for action, title in [
            (
                CREATE,
                "Created:" if not self.dry_run else "To create:",
            ),
            (
                REPLACE,
                "Replaced:" if not self.dry_run else "To replace:",
            ),
        ]:
            section(
                title,
                [
                    describe(e) + "".join(f"\n    {note}" for note in e.notes)
                    for e in self.entries.values()
                    if e.action == action
                ],
            )
        section(
            "Skipped:",
            [
                f"{describe(e)}: {e.reason}"
                for e in self.entries.values()
                if e.action == SKIP
            ],
        )
        section(
            "FAILED:",
            [
                f"{describe(e)}: {e.reason}"
                for e in self.entries.values()
                if e.action == FAIL
            ],
        )
        section(
            "Static paths, set one again where it's wanted:",
            [f"{_describe(page)}" for page in self.problems["static_paths"]],
        )
        section(
            f"References left in another language than {self.target}:",
            self._references("references"),
        )
        section(
            f"Referenced objects with a region per language, without {self.target} plugins:",
            self._references("regions"),
        )
        section(
            f"Referenced objects with empty {self.target} fields:",
            self._references("translated_fields"),
        )
        return "\n".join(lines).strip() + "\n"

    def _references(self, key):
        """Problems found on references, of the objects which were loaded"""
        lines = set()
        for entry, field, text in self.problems[key]:
            if entry.action == FAIL:
                continue
            target = entry.target if entry.target and entry.target.pk else None
            lines.add(f"{_describe(target or entry.original)}: {field} -> {text}")
        return sorted(lines)

    @property
    def failed(self):
        return [e for e in self.entries.values() if e.action == FAIL]


@dataclasses.dataclass
class _Saved:
    object: object
    m2m_data: dict


def _absolute_url(obj):
    try:
        return obj.get_absolute_url()
    except Exception:  # noqa: BLE001
        # Whatever breaks here, the object is then just not linkable.
        return None


def reverse_admin():
    try:
        return reverse("admin:index")
    except NoReverseMatch:
        return "/\0"


def _jmes_to_path(path):
    """``items[*].page`` -> ``items[].page``; only this subset is supported"""
    return path.replace("[*]", "[]")


# Reading the input files ####################################################


def read_translated(path_or_data):
    if isinstance(path_or_data, dict):
        data = path_or_data
    else:
        with open(path_or_data, encoding="utf-8") as f:
            data = json.load(f)
    if data.get("feincms3_autotranslate") != 1:
        raise CommandError(
            f"{path_or_data} isn't a translated file. Dumps go through"
            f" f3autotranslate first (or f3autotranslateload --plan)."
        )
    return data


def parse_into(values):
    """``label:pk=pk`` pairs and load logs into ``{(label, pk): pk}``"""
    result = {}
    for value in values or ():
        if ":" in value and "=" in value and not value.endswith(".json"):
            model, _sep, pair = value.partition(":")
            original, _sep, copy = pair.partition("=")
            try:
                result[(model.lower(), int(original))] = int(copy)
            except ValueError:
                raise CommandError(f"Invalid --into {value!r}.") from None
            continue
        with open(value, encoding="utf-8") as f:
            log = json.load(f)
        if log.get("feincms3_autotranslate_log") != 1:
            raise CommandError(f"{value} isn't a load log.")
        for item in log["objects"]:
            if item.get("copy") is not None and "region" not in item:
                result[(item["model"], item["original"])] = item["copy"]
    return result


def _all_objects(dumps):
    try:
        return all_objects(dumps)
    except DumpError as exc:
        raise CommandError(str(exc)) from exc


def run_from_dumps(dumps, *, target, **kwargs):
    return Run(_all_objects(dumps), target=target, **kwargs)


def run_from_translated(data, **kwargs):
    statuses = data.get("units", {})
    return Run(
        _all_objects(data["dumps"]),
        target=data["target"],
        statuses=statuses,
        **kwargs,
    )


__all__ = [
    "CREATE",
    "FAIL",
    "OWNER",
    "REPLACE",
    "SKIP",
    "Run",
    "parse_into",
    "read_translated",
    "run_from_dumps",
    "run_from_translated",
]
