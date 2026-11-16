"""
The path notation

Paths go on from a field with dots, and ``[]`` stands for every item of a
list: ``data.figures[].title``. A concrete path has indexes instead:
``data.figures[0].title``.
"""

import re


def tokens(path):
    """``data.figures[].title`` -> ``["data", None, "figures", ...]``"""
    return [
        None if token == "[]" else token
        for token in re.findall(r"\[\]|[^.\[\]]+", path)
    ]


def iter_path(fields, path):
    """
    Yield ``(concrete path, container, key)`` for every value at ``path``

    ``fields`` is a dict of field values, or anything else subscriptable.
    ``container[key]`` is the value, and assigning to it changes it in place.
    """

    def walk(container, key, concrete, tokens):
        if not tokens:
            yield concrete, container, key
            return
        value = container[key]
        token, *rest = tokens
        if token is None:
            if isinstance(value, list):
                for index in range(len(value)):
                    yield from walk(value, index, f"{concrete}[{index}]", rest)
        elif isinstance(value, dict) and token in value:
            yield from walk(value, token, f"{concrete}.{token}", rest)

    first, *rest = tokens(path)
    if first in fields:
        yield from walk(fields, first, first, rest)
