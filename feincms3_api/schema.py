"""The schema: resources, region sets, allowed plugins, the types' fields"""

from feincms3_api.codecs import label
from feincms3_api.errors import NotFoundError
from feincms3_api.resources import allowed_types, region_info


class SchemaMixin:
    def schema(self, request):
        resources, types = [], {}
        for resource in self.resources:
            if not resource.has_view_permission(request):
                continue
            inlines = resource.plugin_inlines(request)
            region_sets = []
            for region_set in resource.region_sets():
                keys = [region.key for region in region_set["regions"]]
                allowed = allowed_types(inlines, keys)
                region_sets.append(
                    {
                        "key": region_set["key"],
                        "title": region_set["title"],
                        "regions": [
                            region_info(region) | {"plugins": allowed[region.key]}
                            for region in region_set["regions"]
                        ],
                    }
                )
            resources.append(
                {
                    "name": resource.name,
                    "type": resource.type,
                    "title": str(resource.model._meta.verbose_name),
                    "filters": resource.filters(),
                    "search": resource.searchable(request),
                    "findable": resource.findable,
                    "move_positions": resource.move_positions(),
                    "region_sets": region_sets,
                    "inlines": [
                        label(inline.model)
                        for inline in resource.other_inlines(request)
                    ],
                }
            )
            types[resource.type] = {
                "title": str(resource.model._meta.verbose_name),
                "resource": resource.name,
            }
            for inline in inlines:
                types[label(inline.model)] = {
                    "title": str(inline.model._meta.verbose_name),
                    "plugin_of": resource.type,
                    "container": bool(getattr(inline.model, "container", False)),
                }
            for inline in resource.other_inlines(request):
                types[label(inline.model)] = {
                    "title": str(inline.model._meta.verbose_name),
                    "inline_of": resource.type,
                }
        return {"resources": resources, "types": types}

    def contexts(self, request):
        """``(type, context)`` of every type the user may see"""
        for resource in self.resources:
            if not resource.has_view_permission(request):
                continue
            yield resource.type, resource.context(request, None, self)
            for inline in [
                *resource.plugin_inlines(request),
                *resource.other_inlines(request),
            ]:
                yield (
                    label(inline.model),
                    resource.inline_context(request, inline, None, self),
                )

    def type_schema(self, request, type_label):
        for type, context in self.contexts(request):
            if type == type_label:
                return {
                    "$schema": "https://json-schema.org/draft/2020-12/schema"
                } | self.fields_schema(request, type, context)
        raise NotFoundError(f"No type {type_label!r}.")

    def fields_schema(self, request, type_label, context):
        """The JSON schema of a type's ``fields``, from ``contexts()``"""
        schema = self.adapters.lookup(type_label).json_schema(context)
        for resource in self.resources:
            if resource.type == type_label and (
                names := resource.required_fields(request, self)
            ):
                required = schema.setdefault("required", [])
                required.extend(n for n in names if n not in required)
        return schema
