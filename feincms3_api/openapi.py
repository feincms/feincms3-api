"""
Typed components of the OpenAPI schema, from the types' JSON schemas

The routes take and return plain objects, which the operations validate. The
OpenAPI schema refers to named components for them (``TestappPage``,
``TestappPageWrite``, ``TestappButton``, …), built when the schema is
requested, not at startup, from the types as a superuser sees them: a
superset of what a given user sees, for whom ``/schema/{type}/`` is exact.
"""

import functools
import re

from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.test import RequestFactory
from django.utils.translation import get_language
from pydantic_core import core_schema

from feincms3_api.codecs import label
from feincms3_api.resources import allowed_types


def component_name(type_label):
    """``testapp.jsonplugin_key_figures`` → ``TestappJsonpluginKeyFigures``"""
    return "".join(
        part[:1].upper() + part[1:] for part in re.split(r"[._-]+", type_label)
    )


class _Superuser(AnonymousUser):
    is_active = is_staff = is_superuser = True
    is_anonymous = False
    is_authenticated = True

    def has_perm(self, perm, obj=None):
        return True

    def has_perms(self, perm_list, obj=None):
        return True

    def has_module_perms(self, module):
        return True


def superuser_request():
    request = RequestFactory().get("/")
    request.user = _Superuser()
    return request


def pk_schema(model):
    pk = model._meta.pk
    while isinstance(pk, models.OneToOneField):
        pk = pk.target_field
    if isinstance(pk, models.IntegerField):
        return {"type": "integer"}
    return {"type": "string"}


def rebase(schema, base):
    """Rewrite a schema's own references (``#/$defs/…``) to below ``base``"""
    if isinstance(schema, dict):
        return {
            key: base + value[2:]
            if key == "$ref" and isinstance(value, str) and value.startswith("#/")
            else rebase(value, base)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [rebase(item, base) for item in schema]
    return schema


WARNING = {
    "type": "object",
    "properties": {
        "level": {"enum": ["warning", "error"]},
        "message": {"type": "string"},
        "path": {"type": "string"},
    },
    "required": ["level", "message"],
}

ERROR = {
    "type": "object",
    "description": "`errors` (with their paths) on 422; other keys"
    " depending on the error",
    "properties": {
        "detail": {"type": "string"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["path", "message"],
            },
        },
    },
    "required": ["detail"],
}

REGION = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "title": {"type": "string"},
        "inherited": {"type": "boolean"},
    },
    "required": ["key", "title", "inherited"],
}


class Components:
    def __init__(self, api):
        self.api = api
        self._builders = {}
        self._annotations = {}

    # Annotations ############################################################

    def annotation(self, name, validator=None):
        """
        A type for a route's body or response: an object (validated by
        ``validator``, a pydantic model, if given), documented as the
        component ``name``
        """
        key = (name, validator)
        if key in self._annotations:
            return self._annotations[key]
        components = self

        class Annotation:
            @classmethod
            def __get_pydantic_core_schema__(cls, source, handler):
                return components.core_schema(name, validator)

        Annotation.__name__ = Annotation.__qualname__ = name
        self._annotations[key] = Annotation
        return Annotation

    def core_schema(self, name, validator=None):
        metadata = {
            "pydantic_js_functions": [
                lambda _schema, handler: self.json_schema(name, handler)
            ]
        }
        if validator is None:
            return core_schema.dict_schema(ref=name, metadata=metadata)
        return core_schema.no_info_after_validator_function(
            lambda value: value.model_dump(exclude_unset=True),
            validator.__pydantic_core_schema__,
            ref=name,
            metadata=metadata,
        )

    def json_schema(self, name, handler):
        # The handler generates this schema; the generator registers the
        # others as definitions and returns a $ref to them. Definitions are
        # registered when done, so a component being built (a plugin's
        # children) is referenced by name.
        generator = handler.generate_json_schema
        building = generator.__dict__.setdefault("_feincms3_api_building", set())

        def ref(other):
            if other in building:
                schema = core_schema.definition_reference_schema(other)
            else:
                schema = self.core_schema(other)
            return generator.generate_inner(schema)

        building.add(name)
        try:
            return self.builders()[name](ref)
        finally:
            building.discard(name)

    # The components ########################################################

    def builders(self):
        """``{name: build(ref)}``, per language (titles are translated)"""
        language = get_language()
        if language not in self._builders:
            self._builders[language] = self.build(superuser_request())
        return self._builders[language]

    def build(self, request):
        api = self.api
        fields = {
            type_label: api.fields_schema(request, type_label, context)
            for type_label, context in api.contexts(request)
        }
        builders = {
            "Error": lambda ref: ERROR,
            "Warning": lambda ref: WARNING,
            "Meta": lambda ref: {
                "type": "object",
                "description": "What the object's resource says about it"
                " (`describe()`), its ETag and its warnings",
                "properties": {
                    "etag": {"type": "string"},
                    "warnings": {"type": "array", "items": ref("Warning")},
                    "regions": {"type": "array", "items": REGION},
                    "title": {"type": "string"},
                    "url": {"type": ["string", "null"]},
                    "admin_url": {"type": ["string", "null"]},
                    "references": {"type": "object"},
                },
                "required": ["etag", "warnings"],
            },
            "PluginWrite": lambda ref: {
                "type": "object",
                "description": "Fields of the plugin's type",
                "properties": {"fields": {"type": "object"}},
                "required": ["fields"],
            },
        }

        def add_fields(type_label):
            name = component_name(type_label)
            builders[f"{name}Fields"] = lambda ref: rebase(
                fields[type_label], f"#/components/schemas/{name}Fields/"
            )
            return name

        for resource in api.resources:
            self.add_resource(builders, add_fields, request, resource)

        names = [component_name(resource.type) for resource in api.resources]
        builders["Ref"] = lambda ref: {
            "type": "object",
            "description": "The id of the object created earlier in the batch"
            " with this `ref`",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
            "additionalProperties": False,
        }
        builders["Batch"] = lambda ref: {
            "type": "object",
            "description": 'Run in order, in one transaction. `{"ref": name}`'
            " stands for the id of an object created earlier in the batch,"
            " wherever an id goes. The schemas of `fields` and `content` are"
            ' those of single writes, which don\'t list `{"ref": name}` for'
            " references inside them; the server takes it there too.",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "oneOf": [ref(f"{name}BatchOperation") for name in names]
                    },
                }
            },
            "required": ["operations"],
        }
        builders["BatchResult"] = lambda ref: {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "description": "What each operation returns, `null` for deletions",
                    "items": {"type": ["object", "null"]},
                },
                "refs": {
                    "type": "object",
                    "description": "The ids of the objects with a `ref`, `null`"
                    " in a dry run",
                    "additionalProperties": {"type": ["integer", "string", "null"]},
                },
            },
            "required": ["results", "refs"],
        }
        return builders

    def add_resource(self, builders, add_fields, request, resource):
        name = add_fields(resource.type)
        plugin_inlines = resource.plugin_inlines(request)
        plugins = {
            label(inline.model): (add_fields(label(inline.model)), inline.model)
            for inline in plugin_inlines
        }
        others = {
            label(inline.model): (add_fields(label(inline.model)), inline.model)
            for inline in resource.other_inlines(request)
        }
        regions = {}
        for region_set in resource.region_sets():
            keys = [region.key for region in region_set["regions"]]
            for key, types in allowed_types(plugin_inlines, keys).items():
                regions.setdefault(key, [])
                regions[key].extend(t for t in types if t not in regions[key])

        def one_of(types, ref):
            refs = {type_label: ref(plugins[type_label][0]) for type_label in types}
            return {
                "oneOf": list(refs.values()),
                "discriminator": {
                    "propertyName": "type",
                    "mapping": {
                        type_label: schema["$ref"]
                        for type_label, schema in refs.items()
                        if "$ref" in schema
                    },
                },
            }

        def plugin_schema(ref, type_label, plugin, model):
            return {
                "type": "object",
                "title": str(model._meta.verbose_name),
                "description": "`id` is left out for new plugins; `region`"
                " is only in a single plugin's response, `children` instead"
                " of `depth` with `nested=true`",
                "properties": {
                    "type": {"const": type_label},
                    "id": pk_schema(model),
                    "region": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 0},
                    "fields": ref(f"{plugin}Fields"),
                    "children": {"type": "array", "items": ref(f"{name}Plugin")},
                    "meta": ref("Meta"),
                },
                "required": ["type", "fields"],
            }

        def inline_schema(ref, other, model):
            return {
                "type": "object",
                "title": str(model._meta.verbose_name),
                "properties": {
                    "id": pk_schema(model),
                    "fields": ref(f"{other}Fields"),
                },
                "required": ["fields"],
            }

        for type_label, (plugin, model) in plugins.items():
            builders[plugin] = functools.partial(
                plugin_schema, type_label=type_label, plugin=plugin, model=model
            )
        for other, model in others.values():
            builders[other] = functools.partial(inline_schema, other=other, model=model)

        builders[f"{name}Plugin"] = lambda ref: (
            one_of(plugins, ref) if plugins else {"type": "object"}
        )
        builders[f"{name}Content"] = lambda ref: {
            "type": "object",
            "description": "Plugins per region",
            "properties": {
                key: {"type": "array", "items": one_of(types, ref)}
                for key, types in regions.items()
            },
            "additionalProperties": {
                "type": "array",
                "items": ref(f"{name}Plugin"),
            },
        }

        def body(ref):
            properties = {"fields": ref(f"{name}Fields")}
            if plugins:
                properties["content"] = ref(f"{name}Content")
            if others:
                properties["inlines"] = {
                    "type": "object",
                    "properties": {
                        type_label: {"type": "array", "items": ref(other)}
                        for type_label, (other, _model) in others.items()
                    },
                }
            return properties

        def batch_operation(ref):
            target = {
                "resource": {"const": resource.name},
                "id": {"oneOf": [pk_schema(resource.model), ref("Ref")]},
                "etag": {
                    "type": "string",
                    "description": "Not needed for objects written earlier"
                    " in the batch",
                },
            }

            def operation(op, properties, required=()):
                return {
                    "type": "object",
                    "properties": {"op": {"const": op}} | properties,
                    "required": ["op", "resource", *required],
                    "additionalProperties": False,
                }

            operations = [
                operation(
                    "create",
                    {
                        "resource": target["resource"],
                        "ref": {"type": "string"},
                        **body(ref),
                    },
                ),
                operation("update", target | body(ref), ["id"]),
                operation("delete", target, ["id"]),
            ]
            if plugins:
                operations += [
                    operation(
                        "replace_content",
                        target | {"content": ref(f"{name}Content")},
                        ["id", "content"],
                    ),
                    operation(
                        "update_plugin",
                        target
                        | {
                            "plugin": {
                                "type": "object",
                                "properties": {
                                    "type": {"enum": list(plugins)},
                                    "id": {"type": ["integer", "string"]},
                                },
                                "required": ["type", "id"],
                            },
                            "fields": {"type": "object"},
                        },
                        ["id", "plugin", "fields"],
                    ),
                ]
            return {"oneOf": operations}

        builders[f"{name}BatchOperation"] = batch_operation
        builders[name] = lambda ref: {
            "type": "object",
            "title": str(resource.model._meta.verbose_name),
            "properties": {
                "type": {"const": resource.type},
                "id": pk_schema(resource.model),
                **body(ref),
                "meta": ref("Meta"),
            },
            "required": ["type", "id", "fields", "meta"],
        }
        builders[f"{name}ContentWrite"] = lambda ref: {
            "type": "object",
            "properties": {"content": ref(f"{name}Content")},
            "required": ["content"],
        }
        builders[f"{name}Write"] = lambda ref: {
            "type": "object",
            "properties": body(ref),
        }
        builders[f"{name}List"] = lambda ref: {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "items": {"type": "array", "items": ref(name)},
            },
            "required": ["count", "items"],
        }
