import json

from django.core.management.base import BaseCommand, CommandError

from feincms3_autotranslate.loader import (
    parse_into,
    read_translated,
    run_from_dumps,
    run_from_translated,
)
from feincms3_autotranslate.strings import DumpError, read_dumps


class Command(BaseCommand):
    help = (
        "Loads a translated file as inactive copies (active with --activate),"
        " or plans what dumps would need translating (--plan). The log goes to"
        " stdout, the report to stderr."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "files", nargs="+", help="A translated file, or dumps with --plan."
        )
        parser.add_argument(
            "--plan",
            action="store_true",
            help="Decide for each object of the dumps, write nothing.",
        )
        parser.add_argument("--target", help="The target language (with --plan).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Load inside a transaction which is rolled back.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace the content of existing translations. Corrections are lost.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help=(
                "Create the copies active (is_active = True) instead of inactive."
                " They go live without a review in the admin."
            ),
        )
        parser.add_argument(
            "--into",
            action="append",
            metavar="MODEL:PK=PK|LOG",
            help="Name an existing copy, or take the pairs from a load log.",
        )
        parser.add_argument(
            "--parent", type=int, help="The parent of the top pages in the target."
        )

    def handle(self, *args, **options):
        kwargs = {
            "replace": options["replace"],
            "activate": options["activate"],
            "into": parse_into(options["into"]),
            "parent": options["parent"],
        }
        if options["plan"]:
            if not options["target"]:
                raise CommandError("--plan needs --target.")
            try:
                dumps = read_dumps(options["files"])
            except DumpError as exc:
                raise CommandError(str(exc)) from exc
            run = run_from_dumps(dumps, target=options["target"], **kwargs)
            plan, summary = run.plan()
            self.stdout.write(json.dumps(plan, indent=1))
            self.stderr.write(summary)
            return

        if len(options["files"]) != 1:
            raise CommandError("Load one translated file at a time.")
        data = read_translated(options["files"][0])
        if options["target"] and options["target"] != data["target"]:
            raise CommandError(
                f"The file is translated into {data['target']}, not {options['target']}."
            )
        run = run_from_translated(data, **kwargs)
        log = run.load(dry_run=options["dry_run"])
        if data.get("model"):
            log["model"] = data["model"]
        self.stdout.write(json.dumps(log, indent=1))
        heading = "DRY RUN, nothing has been kept." if options["dry_run"] else "Loaded."
        self.stderr.write(f"{heading}\n{run.report()}")
