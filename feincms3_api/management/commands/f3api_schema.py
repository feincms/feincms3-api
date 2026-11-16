from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from feincms3_api.snapshot import snapshot


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
