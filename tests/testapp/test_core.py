from django.test import SimpleTestCase

from feincms3_api.kinds import HTML, LINK, MACHINE, TEXT, json_string_kind
from feincms3_api.paths import iter_path


class KindsTest(SimpleTestCase):
    def test_json_string_kind(self):
        cases = [
            ("data.title", {"type": "string"}, TEXT),
            ("data.text", {"type": "string", "format": "textarea"}, TEXT),
            ("data.text", {"type": "string", "format": "prose"}, HTML),
            ("data.button_url", {"type": "string"}, LINK),
            ("data.links[]", {"type": "string", "format": "uri"}, LINK),
            ("data.layout", {"type": "string", "enum": ["a"]}, MACHINE),
            ("data.id", {"type": "string", "pattern": "^[a-z]+$"}, MACHINE),
            ("data.page", {"type": "string", "format": "foreign_key"}, MACHINE),
            ("data.number", {"type": "number"}, None),
        ]
        for path, schema, kind in cases:
            with self.subTest(path=path, schema=schema):
                self.assertEqual(json_string_kind(path, schema), kind)

    def test_json_string_kind_is_link(self):
        self.assertEqual(
            json_string_kind(
                "data.target", {"type": "string"}, is_link=lambda p: p == "data.target"
            ),
            LINK,
        )


class PathsTest(SimpleTestCase):
    def test_iter_path(self):
        fields = {"data": {"figures": [{"title": "a"}, {"title": "b"}, {}]}}
        found = [
            (concrete, container[key])
            for concrete, container, key in iter_path(fields, "data.figures[].title")
        ]
        self.assertEqual(
            found, [("data.figures[0].title", "a"), ("data.figures[1].title", "b")]
        )
        self.assertEqual(list(iter_path(fields, "missing.title")), [])
