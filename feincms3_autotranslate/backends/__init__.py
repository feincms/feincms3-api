"""
Translators

A translator turns ``{key: text}`` into ``{key: translated text}`` for one
unit, and raises ``TranslationError`` when it can't. ``BackendError`` stops
the whole run (no credentials, an unknown model), since every further unit
would fail the same way.
"""

import json

from django.utils.module_loading import import_string

from feincms3_autotranslate.conf import config


class TranslationError(Exception):
    """This unit failed, the run goes on"""


class BackendError(Exception):
    """Nothing will work, stop"""


def rules(*, source, target, glossary, instructions, html_keys):
    """The instructions every request carries, for people and for models"""
    lines = [
        f"Translate the values of the JSON object from {source} into {target}.",
        "Answer with a JSON object with exactly the same keys, and nothing else.",
        (
            "Translate every value. Keep the meaning, register and length close to"
            " the original. Keep numbers, dates, email addresses and URLs as they are."
        ),
    ]
    if html_keys:
        lines.append(
            "Some values are HTML. Translate only the text between the tags."
            " Keep every tag, every attribute (href in particular) and every"
            " entity exactly as they are, in the same order."
        )
    if glossary.get("keep"):
        lines.append(
            "Never translate these terms: " + ", ".join(glossary["keep"]) + "."
        )
    if glossary.get("terms"):
        lines.append(
            "Use these translations: "
            + "; ".join(f"{a} -> {b}" for a, b in glossary["terms"].items())
            + "."
        )
    if instructions:
        lines.append(instructions)
    return "\n".join(lines)


def parse_answer(text):
    """The JSON object of an answer, tolerating a Markdown code fence"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise TranslationError(f"The answer isn't valid JSON: {exc}") from exc
    if not isinstance(answer, dict):
        raise TranslationError("The answer isn't a JSON object.")
    return answer


def get_backend(*, model=None):
    path = config()["BACKEND"]
    if not path:
        raise BackendError(
            'FEINCMS3_AUTOTRANSLATE["BACKEND"] isn\'t set. Use --prepare and'
            " --apply to translate without one."
        )
    return import_string(path)(model=model or config()["MODEL"])
