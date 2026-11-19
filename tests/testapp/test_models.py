"""Content models other than pages: feincms3-forms, a tree without paths"""

from testapp.models import (
    Chapter,
    ChapterText,
    Email,
    Form,
    FormRichText,
    Rating,
    SimpleField,
    Text,
    Textbook,
)
from testapp.test_write import WriteTestCase


class FormsTest(WriteTestCase):
    def create_form(self):
        response = self.send(
            "post",
            "resources/testapp.form/",
            {
                "fields": {
                    "name": "Feedback",
                    "form_type": "contact",
                    "language_code": "de",
                },
                "content": {
                    "form": [
                        {
                            "type": "testapp.simplefield_text",
                            "fields": {"name": "name", "label": "Name"},
                        },
                        {
                            "type": "testapp.formrichtext",
                            "fields": {"text": "<p>Und</p>"},
                        },
                        {
                            "type": "testapp.rating",
                            "fields": {
                                "name": "stars",
                                "label": "Wie?",
                                "max_stars": 3,
                            },
                        },
                        {
                            "type": "testapp.simplefield_select",
                            "fields": {
                                "name": "topic",
                                "label": "Thema",
                                "choices": "A\nB",
                            },
                        },
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_proxies_and_other_plugins(self):
        data = self.create_form()
        self.assertEqual(
            [plugin["type"] for plugin in data["content"]["form"]],
            [
                "testapp.simplefield_text",
                "testapp.formrichtext",
                "testapp.rating",
                "testapp.simplefield_select",
            ],
        )
        form = Form.objects.get(pk=data["id"])
        # The proxies share a table, each with its type
        self.assertEqual(
            sorted(SimpleField.objects.filter(parent=form).values_list("type", "name")),
            [("select", "topic"), ("text", "name")],
        )
        self.assertEqual(Rating.objects.get(parent=form).max_stars, 3)
        self.assertEqual(FormRichText.objects.filter(parent=form).count(), 1)
        # The form type's validation, as the admin shows it
        [error] = data["meta"]["warnings"]
        self.assertEqual(error["level"], "error")
        self.assertIn("'email'", error["message"])

        # One proxy's plugin is found under its own type only
        select = SimpleField.objects.get(parent=form, type="select")
        path = f"resources/testapp.form/{form.pk}/"
        self.assertEqual(
            self.get(
                f"{path}plugins/testapp.simplefield_text/{select.pk}/"
            ).status_code,
            404,
        )
        response = self.send(
            "patch",
            f"{path}plugins/testapp.simplefield_select/{select.pk}/",
            {"fields": {"label": "Worum geht es?"}},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 200, response.content)
        select.refresh_from_db()
        self.assertEqual((select.label, select.type), ("Worum geht es?", "select"))
        meta = response.json()["meta"]
        self.assertEqual(meta["etag"], response["ETag"])
        self.assertEqual(meta["warnings"], data["meta"]["warnings"])

        # The response tells what the change left to fix
        response = self.send(
            "patch",
            f"{path}plugins/testapp.simplefield_select/{select.pk}/",
            {"fields": {"name": "name"}},
            etag=response["ETag"],
            dry_run="true",
        )
        self.assertEqual(response.status_code, 200, response.content)
        warnings = response.json()["meta"]["warnings"]
        self.assertEqual(len(warnings), 2)
        self.assertIn("'name' (2)", warnings[0]["message"])

    def test_validation(self):
        data = self.create_form()
        form = Form.objects.get(pk=data["id"])
        Email.objects.create(parent=form, region="form", ordering=50, name="email")
        Text.objects.create(
            parent=form, region="form", ordering=60, name="stars", label="Sterne"
        )
        warnings = self.get(f"resources/testapp.form/{form.pk}/").json()["meta"][
            "warnings"
        ]
        # The email field has no inline, the name "stars" is used twice
        self.assertEqual(
            [(w["level"], w.get("path")) for w in warnings],
            [("warning", "content"), ("warning", None)],
        )
        self.assertIn("'stars' (2)", warnings[1]["message"])

        Form.objects.filter(pk=form.pk).update(form_type="gone")
        warnings = self.get(f"resources/testapp.form/{form.pk}/").json()["meta"][
            "warnings"
        ]
        self.assertEqual(warnings[-1]["level"], "error")
        self.assertEqual(warnings[-1]["path"], "fields.form_type")

    def test_schema(self):
        data = self.get("schema/").json()
        form = next(r for r in data["resources"] if r["name"] == "testapp.form")
        self.assertEqual(
            form["region_sets"],
            [
                {
                    "key": "contact",
                    "title": "contact",
                    "regions": [
                        {
                            "key": "form",
                            "title": "form",
                            "inherited": False,
                            "plugins": [
                                "testapp.simplefield_text",
                                "testapp.simplefield_select",
                                "testapp.formrichtext",
                                "testapp.rating",
                            ],
                        }
                    ],
                }
            ],
        )


class ChaptersTest(WriteTestCase):
    def setUp(self):
        super().setUp()
        self.book = Textbook.objects.create(title="Physik")
        self.mechanics = Chapter.objects.create(textbook=self.book, title="Mechanik")
        self.levers = Chapter.objects.create(
            textbook=self.book, title="Hebel", parent=self.mechanics
        )

    def test_create_and_move(self):
        response = self.send(
            "post",
            "resources/testapp.chapter/",
            {
                "fields": {
                    "textbook": self.book.pk,
                    "title": "Rollen",
                    "parent": self.mechanics.pk,
                    "position": 20,
                },
                "content": {
                    "main": [
                        {
                            "type": "testapp.chaptertext",
                            "fields": {"text": "<p>Zug</p>"},
                        }
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        pulleys = Chapter.objects.get(pk=response.json()["id"])
        self.assertEqual(pulleys.parent, self.mechanics)
        self.assertEqual(ChapterText.objects.get(parent=pulleys).text, "<p>Zug</p>")
        self.assertNotIn("path", response.json()["meta"])

        data = self.get("resources/testapp.chapter/", parent=self.mechanics.pk).json()
        self.assertEqual(
            [item["id"] for item in data["items"]], [self.levers.pk, pulleys.pk]
        )

        # Moved to the top level
        path = f"resources/testapp.chapter/{pulleys.pk}/"
        response = self.send(
            "patch", path, {"fields": {"parent": None}}, etag=self.etag(path)
        )
        self.assertEqual(response.status_code, 200, response.content)
        pulleys.refresh_from_db()
        self.assertIsNone(pulleys.parent)

        # Not below itself
        response = self.send(
            "patch",
            f"resources/testapp.chapter/{self.mechanics.pk}/",
            {"fields": {"parent": self.levers.pk}},
            etag="*",
        )
        self.assertEqual(response.status_code, 422, response.content)

    def test_delete_with_children(self):
        response = self.send(
            "delete", f"resources/testapp.chapter/{self.mechanics.pk}/", etag="*"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            [child["id"] for child in response.json()["children"]], [self.levers.pk]
        )
        self.assertEqual(
            self.send(
                "delete", f"resources/testapp.chapter/{self.levers.pk}/", etag="*"
            ).status_code,
            204,
        )
