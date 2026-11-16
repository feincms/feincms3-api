"""
The strings of a dump, and putting translations back

Pure functions over serialized objects (the ``{"model", "pk", "fields"}``
dicts of a dump): no database, no network. The app registry is needed to
know the models' fields, nothing else.

A unit is what one translation request covers: a translatable object with
its plugins and related objects, or an object with a region per language
with its source region's plugins. Its strings are a flat ``{key: text}``
object, keyed ``<model>.<pk>.<path>``.
"""

import dataclasses
import hashlib
import json
from html.parser import HTMLParser

from django.utils.text import slugify

from feincms3_api.incubator.autotranslate.fields import CHOICES, HTML, LINK, iter_path, specs_for_raw


OWNER = "object"
REGIONS = "regions"


@dataclasses.dataclass
class Unit:
    key: str
    kind: str
    owner: dict
    children: list = dataclasses.field(default_factory=list)

    @property
    def label(self):
        return self.owner["model"]

    @property
    def pk(self):
        return self.owner["pk"]

    def objects(self):
        return [self.owner, *self.children]


class DumpError(Exception):
    pass


def unit_key(label, pk):
    return f"{label}.{pk}"


def group_units(objects, registry):
    """Group the objects of the dumps into units, in dump order"""
    units = {}
    for raw in objects:
        label = raw["model"]
        if label in registry.translatable or label in registry.language_regions:
            kind = OWNER if label in registry.translatable else REGIONS
            key = unit_key(label, raw["pk"])
            if key in units:
                raise DumpError(f"{key} is contained in the dumps twice.")
            units[key] = Unit(key=key, kind=kind, owner=raw)
    for raw in objects:
        label = raw["model"]
        if label in registry.translatable or label in registry.language_regions:
            continue
        if label not in registry.children:
            raise DumpError(
                f"The dumps contain {label}, which no autotranslate dataset"
                f" registers. Only dumps of autotranslate datasets can be"
                f" translated."
            )
        owner_label, name = registry.children[label]
        owner_pk = raw["fields"][name]
        try:
            units[unit_key(owner_label, owner_pk)].children.append(raw)
        except KeyError:
            raise DumpError(
                f"{unit_key(label, raw['pk'])} belongs to"
                f" {unit_key(owner_label, owner_pk)}, which isn't in the dumps."
            ) from None
    return units


def read_dumps(paths_or_data):
    """The objects of several feincms3-data dumps, and their specs"""
    dumps = []
    for item in paths_or_data:
        if isinstance(item, dict):
            data = item
        else:
            with open(item, encoding="utf-8") as f:
                data = json.load(f)
        if "feincms3_autotranslate" in data:
            raise DumpError(
                f"{item} is a translated file, not a dump. Load it with"
                f" f3autotranslateload."
            )
        if data.get("version") != 1:
            raise DumpError(f"{item} isn't a feincms3-data dump.")
        dumps.append({"specs": data["specs"], "objects": data["objects"]})
    return dumps


def all_objects(dumps):
    """
    The objects of all dumps, each once

    Datasets overlap: a tab list used by a page and by an article is in the
    pages dump and in the articles dump. Identical copies are one object;
    differing ones mean the dumps weren't made from the same database state.
    """
    seen = {}
    for dump in dumps:
        for raw in dump["objects"]:
            key = (raw["model"], raw["pk"])
            if key not in seen:
                seen[key] = raw
            elif seen[key] != raw:
                raise DumpError(
                    f"{unit_key(*key)} differs between the dumps; dump them"
                    f" again, one right after the other."
                )
    return list(seen.values())


def source_language(units):
    """The language the units are in, which has to be one"""
    languages = set()
    for unit in units.values():
        if unit.kind == OWNER:
            languages.add(unit.owner["fields"]["language_code"])
        else:
            languages.update(child["fields"]["region"] for child in unit.children)
    if len(languages) > 1:
        raise DumpError(
            f"The dumps contain several languages ({', '.join(sorted(languages))});"
            f" translate one language at a time."
        )
    if not languages:
        raise DumpError("The dumps contain nothing to translate.")
    return languages.pop()


def _choice(line):
    """``(value, label)`` of a feincms3-forms choice line"""
    value, sep, label = line.partition("|")
    if sep:
        return value.strip(), label.strip()
    return slugify(line), line


def object_strings(raw):
    """``{key: (kind, text)}`` for the strings of one serialized object"""
    result = {}
    prefix = f"{raw['model']}.{raw['pk']}"
    for spec in specs_for_raw(raw):
        if spec.kind == LINK:
            continue
        for concrete, container, key in iter_path(raw["fields"], spec.path):
            value = container[key]
            if not isinstance(value, str) or not value.strip():
                continue
            if spec.kind == CHOICES:
                for index, line in enumerate(value.splitlines()):
                    if line.strip():
                        result[f"{prefix}.{concrete}[{index}]"] = (
                            "text",
                            _choice(line)[1],
                        )
            else:
                result[f"{prefix}.{concrete}"] = (spec.kind, value)
    return result


def unit_strings(unit):
    """``{key: text}`` and the keys holding HTML"""
    strings, html = {}, []
    # An object with a region per language is shared, not copied: only its
    # plugins are translated, never its own fields.
    objects = unit.children if unit.kind == REGIONS else unit.objects()
    for raw in objects:
        for key, (kind, text) in object_strings(raw).items():
            strings[key] = text
            if kind == HTML:
                html.append(key)
    return strings, html


def unit_title(unit, registry):
    """A few words saying what the unit is, for people and the translator"""
    config = registry.translatable.get(unit.label) or registry.language_regions.get(
        unit.label
    )
    fields = unit.owner["fields"]
    title = fields.get(config.title_field) or fields.get("title") or ""
    return f"{unit.label} {title}".strip()


def strings_hash(strings):
    return hashlib.sha256(
        json.dumps(strings, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def apply_strings(raw, translations):
    """Write the translations of one serialized object into it, in place"""
    prefix = f"{raw['model']}.{raw['pk']}"
    for spec in specs_for_raw(raw):
        if spec.kind == LINK:
            continue
        for concrete, container, key in iter_path(raw["fields"], spec.path):
            value = container[key]
            if not isinstance(value, str):
                continue
            if spec.kind == CHOICES:
                lines = []
                for index, line in enumerate(value.splitlines()):
                    translated = translations.get(f"{prefix}.{concrete}[{index}]")
                    if translated is None:
                        lines.append(line)
                    else:
                        # The value stays, so stored submissions keep meaning
                        # the same.
                        lines.append(f"{_choice(line)[0]}|{translated}")
                container[key] = "\n".join(lines)
            elif (translated := translations.get(f"{prefix}.{concrete}")) is not None:
                container[key] = translated


# Checking answers ###########################################################

# Attributes which are text for people, and may be translated.
TRANSLATABLE_ATTRIBUTES = {"alt", "title", "aria-label"}


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []

    def _attrs(self, attrs):
        return tuple(
            sorted((k, v) for k, v in attrs if k not in TRANSLATABLE_ATTRIBUTES)
        )

    def handle_starttag(self, tag, attrs):
        self.items.append(("<", tag, self._attrs(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.items.append(("<>", tag, self._attrs(attrs)))

    def handle_endtag(self, tag):
        self.items.append((">", tag))


def html_structure(text):
    parser = _Structure()
    parser.feed(text)
    parser.close()
    return parser.items


def check_answer(strings, answer, html_keys):
    """
    Return the problems of a translator's answer, an empty list if there
    are none

    The answer must have the same keys, and HTML the same tags and
    attributes (``alt``, ``title`` and ``aria-label`` excepted).
    """
    if not isinstance(answer, dict):
        return ["The answer isn't an object."]
    problems = []
    if missing := sorted(set(strings) - set(answer)):
        problems.append(f"Missing keys: {', '.join(missing)}")
    if extra := sorted(set(answer) - set(strings)):
        problems.append(f"Unexpected keys: {', '.join(extra)}")
    html_keys = set(html_keys)
    for key, text in strings.items():
        if key not in answer:
            continue
        translated = answer[key]
        if not isinstance(translated, str):
            problems.append(f"{key}: not a string")
        elif not translated.strip():
            problems.append(f"{key}: empty")
        elif key in html_keys and html_structure(text) != html_structure(translated):
            problems.append(f"{key}: the tags differ from the original's")
    return problems


def split_strings(strings, max_chars):
    """Split ``{key: text}`` into parts of at most ``max_chars`` characters"""
    parts, part, size = [], {}, 0
    for key, text in strings.items():
        if part and size + len(text) > max_chars:
            parts.append(part)
            part, size = {}, 0
        part[key] = text
        size += len(text)
    if part:
        parts.append(part)
    return parts
