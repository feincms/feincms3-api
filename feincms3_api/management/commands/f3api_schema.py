from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.utils.module_loading import import_string

from feincms3_api.codecs import describe


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
            lines.append(f"{type} (resource {resource})")
        else:
            container = ", container" if info["container"] else ""
            lines.append(f"{type} (plugin of {info['plugin_of']}{container})")
        for name, field_schema in type_schema["properties"].items():
            lines.extend(f"  {line}" for line in describe(name, field_schema))
        for name, reason in type_schema.get("x-feincms3-excluded", {}).items():
            lines.append(f"  excluded: {name} ({reason})")
        lines.extend(regions.get(type, []))
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = (
        "Print every type a content API exposes, with its fields and their"
        " kinds; commit the output as a snapshot test."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "api",
            nargs="?",
            help="Dotted path to the ContentAPI, needed if there's more than one",
        )

    def handle(self, *, api, **options):
        from feincms3_api.apps import _content_apis  # noqa: PLC0415

        if api:
            content_api = import_string(api)
        else:
            apis = _content_apis()
            if len(apis) != 1:
                raise CommandError(
                    f"Found {len(apis)} content APIs; name one by its dotted path."
                )
            content_api = apis[0]
        self.stdout.write(snapshot(content_api), ending="")
