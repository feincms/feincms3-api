import json
import os

from django.core.management.base import BaseCommand, CommandError

from feincms3_autotranslate.backends import BackendError, get_backend
from feincms3_autotranslate.datasets import registry
from feincms3_autotranslate.fields import list_fields
from feincms3_autotranslate.review import review_html
from feincms3_autotranslate.strings import DumpError, read_dumps
from feincms3_autotranslate.translate import (
    Job,
    read_answers,
    read_plan,
    silence,
    split_prepared,
)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


class Command(BaseCommand):
    help = "Translates the strings of feincms3-data dumps into another language."

    def add_arguments(self, parser):
        parser.add_argument("dumps", nargs="*", help="feincms3-data dumps")
        parser.add_argument("--target", help="The language to translate into.")
        parser.add_argument("-o", "--output", help="The translated file.")
        parser.add_argument(
            "--only",
            metavar="PLAN",
            help="Translate only what the plan creates or replaces.",
        )
        parser.add_argument(
            "--prepare",
            nargs="?",
            const="strings.json",
            metavar="PATH",
            help="Write the strings to translate (default: strings.json), no API.",
        )
        parser.add_argument(
            "--max-chars",
            type=int,
            metavar="N",
            help=(
                "With --prepare: split the strings into several files of at most"
                " N characters each (an object is never split), strings-1.json etc."
            ),
        )
        parser.add_argument(
            "--apply",
            nargs="+",
            metavar="PATH",
            help="Read the translated strings file(s).",
        )
        parser.add_argument("--review", metavar="PATH", help="Write a review file.")
        parser.add_argument("--model", help="The model of the translator backend.")
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Reuse the answers in <output>.partial of an interrupted run.",
        )
        parser.add_argument(
            "--list-fields",
            action="store_true",
            help="Print the fields which are sent, per model.",
        )

    def _check_options(self, options):
        if not options["dumps"]:
            raise CommandError("Name at least one dump.")
        if not options["target"]:
            raise CommandError("--target is required.")
        if options["prepare"] and (options["apply"] or options["output"]):
            raise CommandError("--prepare goes without --apply and -o.")
        if not options["prepare"] and not options["output"]:
            raise CommandError("-o is required.")
        if options["review"] and options["prepare"]:
            raise CommandError("--review needs -o; there is nothing to review yet.")

    def handle(self, *args, **options):
        if options["list_fields"]:
            self.stdout.write(list_fields(registry()), ending="")
            return

        self._check_options(options)
        try:
            only = (
                read_plan(options["only"], target=options["target"])
                if options["only"]
                else None
            )
            job = Job(read_dumps(options["dumps"]), target=options["target"], only=only)
        except DumpError as exc:
            raise CommandError(str(exc)) from exc

        if options["prepare"]:
            prepared = job.prepare()
            if options["max_chars"]:
                paths = []
                for path, part in split_prepared(
                    prepared, options["prepare"], options["max_chars"]
                ):
                    _write_json(path, part)
                    paths.append(path)
                self.stderr.write(f"Wrote {', '.join(paths)}: {job.stats()}.")
            else:
                _write_json(options["prepare"], prepared)
                self.stderr.write(f"Wrote {options['prepare']}: {job.stats()}.")
            return

        try:
            translator = (
                read_answers(options["apply"])
                if options["apply"]
                else get_backend(model=options["model"])
            )
        except (BackendError, DumpError) as exc:
            raise CommandError(str(exc)) from exc

        partial = f"{options['output']}.partial"
        if not options["resume"] and not options["apply"] and os.path.exists(partial):
            raise CommandError(
                f"{partial} exists: an earlier run was interrupted. Continue it"
                f" with --resume, or delete the file."
            )
        if model := getattr(translator, "model", None):
            self.stderr.write(f"Model: {model}")
        self.stderr.write(f"Translating {job.stats()}.")
        try:
            translated = job.run(
                translator,
                partial=None if options["apply"] else partial,
                resume=options["resume"],
                progress=self.stderr.write if options["verbosity"] >= 1 else silence,
            )
        except BackendError as exc:
            raise CommandError(
                f"{exc}\nThe answers so far are in {partial}; continue with --resume."
            ) from exc

        _write_json(options["output"], translated)
        if os.path.exists(partial):
            os.remove(partial)
        if options["review"]:
            with open(options["review"], "w", encoding="utf-8") as f:
                f.write(review_html(job, translated))

        failed = [
            f"  {key}: {state.get('reason', '')}"
            for key, state in translated["units"].items()
            if state["status"] == "failed"
        ]
        self.stderr.write(f"Wrote {options['output']}.")
        if failed:
            self.stderr.write(
                "FAILED, the load leaves these out:\n" + "\n".join(failed)
            )
