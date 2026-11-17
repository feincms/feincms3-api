"""
What a string is: text for people, HTML, a link, or something for machines

Every string the API exposes has a kind, in the schema as
``"x-feincms3-kind"``. A translator translates ``text`` and ``html``, maps
``link``s and keeps ``machine`` strings as they are. ``choices`` is a
feincms3-forms choice list (``value|label`` lines), of which only the
labels are text.

``machine`` strings are read by code: slugs and paths, choices, ids,
anything a pattern constrains. Changing one has effects beyond its wording
(URLs, links to an anchor, stored form submissions), so it may be changed
on purpose, e.g. a slug for a translated page, but never as a side effect
of translating or rewording text.
"""

TEXT = "text"
HTML = "html"
LINK = "link"
MACHINE = "machine"
CHOICES = "choices"

# Never text, whatever their type says. feincms3-meta's ``meta_canonical``
# isn't here: it's a URLField, so it's a link.
MACHINE_NAMES = {
    "app_instance_namespace",
    "app_namespace",
    "language_code",
    "menu",
    # django-imagefield's primary point of interest, "0.5x0.5"
    "meta_image_ppoi",
    "meta_robots",
    "page_type",
    "path",
    "region",
    "slug",
    "template_key",
    "type",
}

# String formats of json-editor which are still text for people.
TEXT_FORMATS = {"textarea": TEXT, "prose": HTML}
LINK_FORMATS = {"uri", "url", "uri-reference"}


def is_machine_name(name):
    """Names of strings which are never text, whatever their type says"""
    # django-imagefield's primary points of interest: "0.5x0.5"
    return name in MACHINE_NAMES or name == "ppoi" or name.endswith("_ppoi")


def is_link_name(name):
    """``url``, ``link``, ``*_url`` and ``*_link`` hold a URL, whatever their type"""
    return name in {"url", "link"} or name.endswith(("_url", "_link"))


def schema_paths(schema, prefix, name=""):
    """``(path, schema)`` for every value a JSON schema describes"""
    yield prefix, schema
    type = schema.get("type")
    if type == "object":
        for key, sub in schema.get("properties", {}).items():
            yield from schema_paths(sub, f"{prefix}.{key}", key)
    elif type == "array" and isinstance(schema.get("items"), dict):
        yield from schema_paths(schema["items"], f"{prefix}[]", name)


def json_string_kind(path, schema, *, is_link=None):
    """
    The kind of a string in JSON data, ``None`` if ``schema`` isn't a string

    Any ``pattern`` makes a string a machine string, not just a numeric one:
    a pattern constrains the value to something machine-readable, and
    changing it breaks whatever reads it.
    """
    if schema.get("type") != "string":
        return None
    name = path.rsplit(".", 1)[-1].removesuffix("[]")
    format = schema.get("format")
    if is_link_name(name) or format in LINK_FORMATS or (is_link and is_link(path)):
        return LINK
    if "enum" in schema or "pattern" in schema:
        return MACHINE
    if format is None or format in TEXT_FORMATS:
        return TEXT_FORMATS.get(format, TEXT)
    return MACHINE
