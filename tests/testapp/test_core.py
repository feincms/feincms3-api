from unittest import mock

from django.test import SimpleTestCase

from feincms3_api.apps import check_resources
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


class ChecksTest(SimpleTestCase):
    def test_resources(self):
        self.assertEqual(check_resources(), [])

    def test_two_inlines_for_one_model(self):
        from testapp.admin import SessionInline, TrainingAdmin  # noqa: PLC0415

        class OtherSessionInline(SessionInline):
            pass

        with mock.patch.object(
            TrainingAdmin, "inlines", [*TrainingAdmin.inlines, OtherSessionInline]
        ):
            [error] = check_resources()
        self.assertEqual(error.id, "feincms3_api.E003")
        self.assertIn("testapp.session", error.msg)
