"""
The Claude API

Needs ``feincms3-autotranslate[anthropic]``, ``ANTHROPIC_API_KEY`` (or
another credential the SDK finds) in the developer's environment, and a
model: ``FEINCMS3_AUTOTRANSLATE["MODEL"]`` or ``--model``. There is no
default model on purpose.

An object's strings are split into several requests by size; each carries
the rules, the glossary, the instructions and the object's title. An answer
which was cut off, isn't JSON or was refused fails the object. Rate limits,
overloads and server errors are retried by the SDK (``max_retries``).
"""

import json

from feincms3_api.incubator.autotranslate.backends import (
    BackendError,
    TranslationError,
    parse_answer,
    rules,
)
from feincms3_api.incubator.autotranslate.conf import config
from feincms3_api.incubator.autotranslate.strings import split_strings


class Anthropic:
    max_tokens = 64000
    max_retries = 5

    def __init__(self, *, model):
        if not model:
            raise BackendError(
                'The anthropic backend needs a model: FEINCMS3_AUTOTRANSLATE["MODEL"]'
                " or --model. There is no default."
            )
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise BackendError(
                "Install feincms3-autotranslate[anthropic] to use the Claude API."
            ) from exc
        self.anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(max_retries=self.max_retries)

    def translate(
        self, strings, *, source, target, glossary, instructions, title="", html_keys=()
    ):
        system = rules(
            source=source,
            target=target,
            glossary=glossary,
            instructions=instructions,
            html_keys=html_keys,
        )
        answer = {}
        for part in split_strings(strings, config()["MAX_REQUEST_CHARS"]):
            answer |= self._request(system, part, title=title, html_keys=html_keys)
        return answer

    def _request(self, system, strings, *, title, html_keys):
        html = [key for key in html_keys if key in strings]
        content = (
            (f"The strings belong to: {title}\n" if title else "")
            + (f"HTML values: {', '.join(html)}\n" if html else "")
            + json.dumps(strings, ensure_ascii=False, indent=1)
        )
        anthropic = self.anthropic
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                message = stream.get_final_message()
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as exc:
            raise BackendError(f"The Claude API refused: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise TranslationError(
                f"The Claude API answered {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise TranslationError(
                f"The Claude API couldn't be reached: {exc}"
            ) from exc

        if message.stop_reason == "max_tokens":
            raise TranslationError("The answer was cut off at the output limit.")
        if message.stop_reason == "refusal":
            raise TranslationError("The model declined to translate this.")
        text = "".join(block.text for block in message.content if block.type == "text")
        return parse_answer(text)
