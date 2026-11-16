"""
From dumps to a translated file

``translated.json`` holds the originals of the dumps with their original
primary keys and translated texts. It deliberately isn't a feincms3-data
dump: it has no ``"version"`` key, which ``f3loaddata`` rejects, so nobody
writes the French text over the German pages by mistake.
"""

import copy
import json
import os

from feincms3_api.incubator.autotranslate.backends import BackendError, TranslationError
from feincms3_api.incubator.autotranslate.conf import config, load_glossary
from feincms3_api.incubator.autotranslate.datasets import registry as default_registry
from feincms3_api.incubator.autotranslate.strings import (
    DumpError,
    all_objects,
    apply_strings,
    check_answer,
    group_units,
    source_language,
    strings_hash,
    unit_strings,
    unit_title,
)


STRINGS_MARKER = "feincms3_autotranslate_strings"


def silence(*args):
    pass


def read_plan(path, *, target):
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("feincms3_autotranslate_plan") != 1:
        raise DumpError(f"{path} isn't a plan.")
    if plan["target"] != target:
        raise DumpError(f"{path} is a plan for {plan['target']}, not for {target}.")
    return {
        key
        for key, item in plan["objects"].items()
        if item["action"] in {"create", "replace"}
    }


class Job:
    def __init__(self, dumps, *, target, only=None, registry=None):
        self.registry = registry or default_registry()
        self.dumps = dumps
        self.target = target
        self.units = group_units(all_objects(dumps), self.registry)
        self.source = source_language(self.units)
        if self.source == target:
            raise DumpError(
                f"The target language is the source language ({target})."
                f" Copying without translating is out of scope."
            )
        self.only = only
        self.glossary = load_glossary(
            config()["GLOSSARY"], source=self.source, target=target
        )
        self.instructions = config()["INSTRUCTIONS"]

    def wanted(self):
        """The units to translate, with their strings"""
        for key, unit in self.units.items():
            if self.only is not None and key not in self.only:
                continue
            strings, html = unit_strings(unit)
            yield key, unit, strings, html

    def stats(self):
        objects = strings = chars = 0
        for _key, _unit, unit_strings_, _html in self.wanted():
            objects += 1
            strings += len(unit_strings_)
            chars += sum(len(text) for text in unit_strings_.values())
        return f"{objects} objects, {strings} strings, {chars} characters"

    def prepare(self):
        """The strings file for a person or an agent to translate"""
        from feincms3_api.incubator.autotranslate.backends import rules  # noqa: PLC0415

        objects = {}
        any_html = False
        for key, unit, strings, html in self.wanted():
            if not strings:
                continue
            any_html = any_html or bool(html)
            objects[key] = {
                "title": unit_title(unit, self.registry),
                "html": html,
                "strings": strings,
            }
        return {
            STRINGS_MARKER: 1,
            "source": self.source,
            "target": self.target,
            "rules": rules(
                source=self.source,
                target=self.target,
                glossary=self.glossary,
                instructions=self.instructions,
                html_keys=[True] if any_html else [],
            ),
            "glossary": self.glossary,
            "instructions": self.instructions,
            "objects": objects,
        }

    def run(self, translator, *, partial=None, resume=False, progress=silence):
        """
        Translate unit by unit and return the translated file's data

        ``translator`` is a backend, or a ``{unit key: strings}`` dict of
        answers (``--apply``). Each answer goes into ``partial`` (a path) as
        soon as it arrives; with ``resume``, answers in there whose source
        strings haven't changed are reused.
        """
        done = _read_partial(partial) if resume and partial else {}
        if partial and not resume and os.path.exists(partial):
            os.remove(partial)

        translations, statuses = {}, {}
        wanted = list(self.wanted())
        wanted_keys = {key for key, *_rest in wanted}
        for key in self.units:
            if key not in wanted_keys:
                statuses[key] = {"status": "untranslated"}

        for index, (key, unit, strings, html) in enumerate(wanted, 1):
            digest = strings_hash(strings)
            if (previous := done.get(key)) and previous["hash"] == digest:
                state = previous["state"]
                answer = previous.get("strings", {})
                progress(f"[{index}/{len(wanted)}] {key}: from the partial file")
            else:
                state, answer = self._translate_unit(
                    key, unit, strings, html, translator
                )
                progress(f"[{index}/{len(wanted)}] {key}: {state['status']}")
                if partial:
                    _append_partial(
                        partial,
                        {"key": key, "hash": digest, "state": state, "strings": answer},
                    )
            statuses[key] = state
            if state["status"] == "translated":
                translations |= answer

        dumps = copy.deepcopy(self.dumps)
        for dump in dumps:
            for raw in dump["objects"]:
                apply_strings(raw, translations)

        return {
            "feincms3_autotranslate": 1,
            "source": self.source,
            "target": self.target,
            "model": getattr(translator, "model", None),
            "dumps": dumps,
            "units": statuses,
        }

    def _translate_unit(self, key, unit, strings, html, translator):
        if not strings:
            return {"status": "translated"}, {}
        try:
            answer = self._answer(key, unit, strings, html, translator)
        except BackendError:
            raise
        except TranslationError as exc:
            return {"status": "failed", "reason": str(exc)}, {}
        if problems := check_answer(strings, answer, html):
            return {"status": "failed", "reason": " ".join(problems)}, {}
        return {"status": "translated"}, answer

    def _answer(self, key, unit, strings, html, translator):
        if isinstance(translator, dict):
            if key not in translator:
                raise TranslationError("The answers file has no entry for it.")
            return translator[key]
        return translator.translate(
            strings,
            source=self.source,
            target=self.target,
            glossary=self.glossary,
            instructions=self.instructions,
            title=unit_title(unit, self.registry),
            html_keys=html,
        )


def split_prepared(data, path, max_chars):
    """
    ``(path, part)`` for each part of a strings file of at most ``max_chars``
    characters: ``strings.json`` becomes ``strings-1.json``, ... Each part
    carries the rules, so it can be translated on its own, for example by
    several agents at once. An object is never split.
    """
    stem, dot, suffix = str(path).rpartition(".")
    if not dot:
        stem, suffix = suffix, "json"
    parts, objects, size = [], {}, 0
    for key, item in data["objects"].items():
        chars = sum(len(text) for text in item["strings"].values())
        if objects and size + chars > max_chars:
            parts.append(objects)
            objects, size = {}, 0
        objects[key] = item
        size += chars
    if objects:
        parts.append(objects)
    for index, objects in enumerate(parts, 1):
        yield f"{stem}-{index}.{suffix}", data | {"objects": objects}


def read_answers(paths):
    """The ``--apply`` files: strings files with translated strings"""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    answers = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get(STRINGS_MARKER) != 1:
            raise DumpError(f"{path} isn't a strings file written by --prepare.")
        for key, item in data.get("objects", {}).items():
            if key in answers:
                raise DumpError(f"{key} is in several of the --apply files.")
            answers[key] = item.get("strings")
    return answers


def _read_partial(path):
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    item = json.loads(line)
                except ValueError:
                    # A line cut off by the interruption
                    continue
                result[item["key"]] = item
    return result


def _append_partial(path, item):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
