"""
Datasets for translatable content

Each function returns a feincms3-data dataset entry: ``f3dumpdata`` uses its
``"specs"``, and the ``"autotranslate"`` key tells the rest of this package
what the dumped models are, see ``registry()``.

The specs hold primary key lists (``pk__in``, ``parent__in``), so a dump
states exactly which rows it contains.
"""

import dataclasses
from functools import cache, partial

from content_editor.models import PluginBase
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import models

from feincms3_api.incubator.autotranslate.conf import first_language


@dataclasses.dataclass(frozen=True)
class Translatable:
    """An object which is copied into the target language"""

    model: type
    # ``(model, name of its foreign key to the translatable model)``
    related: tuple = ()
    offline: dict | None = None
    language_regions: tuple = ()
    title_field: str = "title"
    tree: bool = False


@dataclasses.dataclass(frozen=True)
class LanguageRegions:
    """An object whose regions are languages, translated into itself"""

    model: type
    title_field: str = "title"
    # Never copied, so neither of these applies.
    related: tuple = ()
    offline: dict | None = None
    tree: bool = False


def label(model):
    return model._meta.label_lower


def has_field(model, name):
    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


@cache
def plugin_models(model):
    """Every concrete django-content-editor plugin whose ``parent`` is ``model``"""
    return [
        m
        for m in apps.get_models()
        if issubclass(m, PluginBase)
        and not m._meta.proxy
        and m._meta.get_field("parent").related_model is model
    ]


def specs_for_plugins(model, spec=None):
    """feincms3-data specs for all plugins of ``model``"""
    spec = {} if spec is None else spec
    return [{**spec, "model": label(m)} for m in plugin_models(model)]


def _related(model, related):
    result = []
    for entry in related:
        rel_model, name = entry if isinstance(entry, tuple) else (entry, None)
        if name is None:
            candidates = [
                f.name
                for f in rel_model._meta.concrete_fields
                if f.many_to_one and f.related_model is model
            ]
            if len(candidates) != 1:
                raise ImproperlyConfigured(
                    f"{label(rel_model)} needs exactly one foreign key to"
                    f" {label(model)}, it has {candidates!r}. Pass"
                    f" ({rel_model.__name__}, '<field name>') instead."
                )
            name = candidates[0]
        result.append((rel_model, name))
    return tuple(result)


def _check_language_regions(model):
    keys = {region.key for region in getattr(model, "regions", ())}
    if not keys:
        raise ImproperlyConfigured(
            f"{label(model)} has no regions, so it can't have a region per language."
        )


def page_tree_dataset(page_model, *, language_regions=()):
    for lr in language_regions:
        _check_language_regions(lr)
    config = Translatable(
        model=page_model,
        language_regions=tuple(language_regions),
        tree=True,
    )
    return {"specs": partial(_page_specs, config), "autotranslate": config}


def object_dataset(
    model, *, related=(), offline=None, language_regions=(), title_field="title"
):
    if not has_field(model, "language_code"):
        raise ImproperlyConfigured(f"{label(model)} has no language_code field.")
    if offline is None and not has_field(model, "is_active"):
        raise ImproperlyConfigured(
            f"{label(model)} has no is_active field. Its copies would go live"
            f" the moment they're loaded; pass offline={{...}} to say how they"
            f" stay offline."
        )
    for lr in language_regions:
        _check_language_regions(lr)
    config = Translatable(
        model=model,
        related=_related(model, related),
        offline=offline,
        language_regions=tuple(language_regions),
        title_field=title_field,
    )
    return {"specs": partial(_object_specs, config), "autotranslate": config}


def language_regions_dataset(model, *, title_field="title"):
    _check_language_regions(model)
    config = LanguageRegions(model=model, title_field=title_field)
    return {"specs": partial(_language_regions_specs, config), "autotranslate": config}


# Selection ##################################################################


@dataclasses.dataclass
class Selection:
    argument: str
    subtree: bool = False
    lang: str = ""
    site: str = ""


def parse_selection(argument):
    """
    ``Angebot``, ``Angebot|subtree``, ``*|lang=fr``, ``17|site=example.com``
    """
    argument, *flags = argument.split("|")
    selection = Selection(argument=argument.strip(), lang=first_language())
    for flag in flags:
        key, sep, value = flag.partition("=")
        if key == "subtree" and not sep:
            selection.subtree = True
        elif key in {"lang", "site"} and sep and value:
            setattr(selection, key, value)
        else:
            raise CommandError(f"Unknown selection option {flag!r}.")
    if not selection.argument:
        raise CommandError("Select something: a title, a slug, a pk or '*'.")
    return selection


def _describe(obj):
    parts = [f"pk={obj.pk}"]
    if site := getattr(obj, "site", None):
        parts.append(f"site={getattr(site, 'host', site)}")
    if language_code := getattr(obj, "language_code", None):
        parts.append(f"language={language_code}")
    if path := getattr(obj, "path", None):
        parts.append(f"path={path}")
    elif slug := getattr(obj, "slug", None):
        parts.append(f"slug={slug}")
    return f"{obj} ({', '.join(parts)})"


def resolve(queryset, argument, *, title_field):
    """
    Find one object: exact title, exact slug, or the pk if the argument is
    all digits
    """
    model = queryset.model
    lookups = []
    if title_field and has_field(model, title_field):
        lookups.append({title_field: argument})
    if has_field(model, "slug"):
        lookups.append({"slug": argument})
    if argument.isdigit():
        lookups.append({"pk": int(argument)})

    for lookup in lookups:
        if matches := list(queryset.filter(**lookup)[:11]):
            break
    else:
        matches = []

    if len(matches) == 1:
        return matches[0]
    name = model._meta.verbose_name
    if matches:
        raise CommandError(
            f"Several {model._meta.verbose_name_plural} match {argument!r}:\n"
            + "\n".join(f"  {_describe(obj)}" for obj in matches)
        )
    candidates = (
        list(queryset.filter(**{f"{title_field}__icontains": argument})[:10])
        if title_field and has_field(model, title_field)
        else []
    )
    raise CommandError(
        f"No {name} matches {argument!r}."
        + (
            "\nDid you mean one of these?\n"
            + "\n".join(f"  {_describe(obj)}" for obj in candidates)
            if candidates
            else ""
        )
    )


def _filter_site(queryset, selection):
    if not selection.site:
        return queryset
    if not has_field(queryset.model, "site"):
        raise CommandError(f"{label(queryset.model)} has no site; drop site=.")
    return queryset.filter(site__host=selection.site)


def _language_region_pks(model, pks, lr_model):
    """Objects of ``lr_model`` the plugins of ``pks`` point at"""
    result = set()
    for plugin in plugin_models(model):
        for field in plugin._meta.concrete_fields:
            if field.many_to_one and field.related_model is lr_model:
                result.update(
                    plugin._default_manager
                    .filter(parent__in=pks)
                    .exclude(**{field.attname: None})
                    .values_list(field.attname, flat=True)
                )
    return sorted(result)


def _object_specs_for_pks(config, pks, lang):
    specs = [
        {"model": label(config.model), "filter": {"pk__in": pks}},
        *specs_for_plugins(config.model, {"filter": {"parent__in": pks}}),
        *(
            {"model": label(rel_model), "filter": {f"{name}__in": pks}}
            for rel_model, name in config.related
        ),
    ]
    for lr_model in config.language_regions:
        if lr_pks := _language_region_pks(config.model, pks, lr_model):
            specs.extend(_language_regions_specs_for_pks(lr_model, lr_pks, lang))
    return specs


def _page_specs(config, argument):
    selection = parse_selection(argument)
    queryset = _filter_site(
        config.model._default_manager.filter(language_code=selection.lang),
        selection,
    )
    if selection.argument == "*":
        # Roots are never copied: /de/ can't become /fr/, which exists.
        pages = queryset.exclude(parent=None)
    else:
        page = resolve(queryset, selection.argument, title_field="title")
        pages = (
            page.descendants(include_self=True)
            if selection.subtree
            else config.model._default_manager.filter(pk=page.pk)
        ).exclude(parent=None)
        if not pages.exists():
            raise CommandError(
                f"{_describe(page)} is a root page, and roots are never copied."
                f" Select the pages below it with '*' or by their titles."
            )
    pks = sorted(pages.values_list("pk", flat=True))
    return _object_specs_for_pks(config, pks, selection.lang)


def _object_specs(config, argument):
    selection = parse_selection(argument)
    if selection.subtree:
        raise CommandError("Only pages have subtrees.")
    queryset = _filter_site(
        config.model._default_manager.filter(language_code=selection.lang),
        selection,
    )
    if selection.argument == "*":
        pks = sorted(queryset.values_list("pk", flat=True))
    else:
        pks = [resolve(queryset, selection.argument, title_field=config.title_field).pk]
    return _object_specs_for_pks(config, pks, selection.lang)


def _language_regions_specs_for_pks(model, pks, lang):
    return [
        {"model": label(model), "filter": {"pk__in": pks}},
        *specs_for_plugins(model, {"filter": {"parent__in": pks, "region": lang}}),
    ]


def _language_regions_specs(config, argument):
    selection = parse_selection(argument)
    if selection.subtree or selection.site:
        raise CommandError("Objects with a region per language take only lang=.")
    queryset = config.model._default_manager.all()
    if selection.argument == "*":
        pks = set()
        for plugin in plugin_models(config.model):
            pks.update(
                plugin._default_manager.filter(region=selection.lang).values_list(
                    "parent", flat=True
                )
            )
        pks = sorted(pks)
    else:
        pks = [resolve(queryset, selection.argument, title_field=config.title_field).pk]
    return _language_regions_specs_for_pks(config.model, pks, selection.lang)


# Registry ###################################################################


@dataclasses.dataclass(frozen=True)
class Registry:
    # label -> Translatable
    translatable: dict
    # label -> LanguageRegions
    language_regions: dict
    # label -> (owner label, name of the foreign key to the owner)
    children: dict

    def models(self):
        """Every model whose rows a run copies, owners first"""
        result = [config.model for config in self.translatable.values()]
        for label_ in self.children:
            result.append(apps.get_model(label_))
        return result


def build_registry(datasets):
    translatable = {}
    language_regions = {}
    for name, dataset in datasets.items():
        config = dataset.get("autotranslate")
        if isinstance(config, Translatable):
            key = label(config.model)
            if key in translatable and translatable[key] != config:
                raise ImproperlyConfigured(
                    f"{key} is registered by several datasets with different"
                    f" options (again in {name!r})."
                )
            translatable[key] = config
            for lr in config.language_regions:
                language_regions.setdefault(label(lr), LanguageRegions(model=lr))
        elif isinstance(config, LanguageRegions):
            language_regions[label(config.model)] = config

    if both := set(translatable) & set(language_regions):
        raise ImproperlyConfigured(
            f"{sorted(both)!r} are registered as translatable objects and as"
            f" objects with a region per language."
        )

    children = {}
    for key, config in [*translatable.items(), *language_regions.items()]:
        for plugin in plugin_models(config.model):
            children[label(plugin)] = (key, "parent")
        for rel_model, name in getattr(config, "related", ()):
            children[label(rel_model)] = (key, name)
    return Registry(
        translatable=translatable,
        language_regions=language_regions,
        children=children,
    )


@cache
def registry():
    from feincms3_data.data import datasets  # noqa: PLC0415

    return build_registry(datasets())


def is_translated_field(field):
    """A column of a django-translated-fields field (``title_de``)"""
    return hasattr(field, "_translated_field_language_code")


def translated_fields(model):
    """``{name: {language: attname}}`` for django-translated-fields fields"""
    result = {}
    for field in model._meta.concrete_fields:
        if language := getattr(field, "_translated_field_language_code", None):
            name = field.name.removesuffix(f"_{language.replace('-', '_')}")
            result.setdefault(name, {})[language] = field.attname
    return result


def has_unique_slug(model):
    """Whether ``slug`` takes part in any uniqueness rule"""
    meta = model._meta
    if not has_field(model, "slug"):
        return False
    if meta.get_field("slug").unique:
        return True
    return any("slug" in fields for fields in meta.unique_together) or any(
        "slug" in constraint.fields
        for constraint in meta.constraints
        if isinstance(constraint, models.UniqueConstraint)
    )
