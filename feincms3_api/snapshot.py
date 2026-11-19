"""
The snapshot: every type with its fields and their kinds, as text

Projects commit it as a test, so a new plugin or field becomes visible
through the API only after someone has looked at the diff.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory

from feincms3_api.codecs import KIND, MACHINE, REF


def system_request():
    """A request as a superuser who exists only in memory"""
    request = RequestFactory().get("/")
    request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
    return request


def snapshot(api):
    """Every type of ``api`` with its fields and their kinds, and the regions"""
    request = system_request()
    schema = api.schema(request)
    regions = {}
    for resource in schema["resources"]:
        lines = regions.setdefault(resource["type"], [])
        for region_set in resource["region_sets"]:
            prefix = f"{region_set['key']}." if region_set["key"] else ""
            for region in region_set["regions"]:
                plugins = ", ".join(region["plugins"]) or "-"
                lines.append(f"  region {prefix}{region['key']}: {plugins}")

    lines = []
    for type, context in sorted(api.contexts(request), key=lambda item: item[0]):
        type_schema = api.adapters.lookup(type).json_schema(context)
        info = schema["types"][type]
        if resource := info.get("resource"):
            # The resource's name, where it isn't the type (the default)
            named = f" {resource}" if resource != type else ""
            lines.append(f"{type} (resource{named})")
        elif inline_of := info.get("inline_of"):
            lines.append(f"{type} (inline of {inline_of})")
        else:
            container = ", container" if info["container"] else ""
            lines.append(f"{type} (plugin of {info['plugin_of']}{container})")
        for name, field_schema in type_schema["properties"].items():
            lines.extend(f"  {line}" for line in describe(name, field_schema))
        for name, reason in type_schema.get("x-feincms3-excluded", {}).items():
            lines.append(f"  excluded: {name} ({reason})")
        lines.extend(regions.get(type, []))
    return "\n".join(lines) + "\n"


def describe(name, schema):
    """
    Lines for the snapshot: ``name: kind`` for this field and, for JSON data,
    for every value inside it
    """
    suffix = " (read-only)" if schema.get("readOnly") else ""
    if _is_leaf(schema):
        return [f"{name}: {_describe_one(schema)}{suffix}"]
    lines = [f"{name}: json{suffix}"]
    lines.extend(
        f"{path}: {_describe_one(sub)}"
        for path, sub in _leaf_paths(schema, name)
        if path != name
    )
    return lines


def _is_leaf(schema):
    return (
        REF in schema
        or schema.get("x-feincms3-file")
        or not {"object", "array"} & set(_types(schema))
        or (schema.get("type") == "array" and REF in schema.get("items", {}))
    )


def _types(schema):
    """``type`` as a list, it can be one (``"integer"``) or several"""
    types = schema.get("type")
    return types if isinstance(types, list) else [types]


def _leaf_paths(schema, prefix):
    if _is_leaf(schema):
        yield prefix, schema
    elif schema.get("type") == "object":
        for key, sub in schema.get("properties", {}).items():
            yield from _leaf_paths(sub, f"{prefix}.{key}")
    elif isinstance(schema.get("items"), dict):
        yield from _leaf_paths(schema["items"], f"{prefix}[]")


def _describe_one(schema):
    if schema.get("x-feincms3-file"):
        return "file"
    if REF in schema:
        return f"ref {schema[REF]}"
    if schema.get("type") == "array" and REF in schema.get("items", {}):
        return f"refs {schema['items'][REF]}"
    if KIND in schema:
        kind = schema[KIND]
        return "choice" if "enum" in schema and kind == MACHINE else kind
    if "enum" in schema:
        return "choice"
    type = schema.get("type")
    if isinstance(type, list):
        type = next((t for t in type if t != "null"), "null")
    return type or "json"
