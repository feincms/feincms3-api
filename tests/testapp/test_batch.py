from django.contrib.admin.models import LogEntry
from django.test import override_settings

from testapp.models import Button, Page, RichText
from testapp.test_write import WriteTestCase


def page(title, slug, parent, **fields):
    return {
        "title": title,
        "slug": slug,
        "parent": parent,
        "page_type": "standard",
        "language_code": "de",
        "is_active": False,
        **fields,
    }


class BatchTest(WriteTestCase):
    def batch(self, operations, **params):
        return self.send("post", "batch/", {"operations": operations}, **params)

    def subtree(self):
        return [
            {
                "op": "create",
                "resource": "pages",
                "ref": "section",
                "fields": page("Bereich", "bereich", self.home.pk),
            },
            {
                "op": "create",
                "resource": "pages",
                "ref": "child",
                "fields": page(
                    "Unterseite",
                    "unterseite",
                    {"type": "testapp.page", "id": {"ref": "section"}},
                ),
                "content": {
                    "main": [
                        {"type": "testapp.richtext", "fields": {"text": "<p>A</p>"}}
                    ]
                },
            },
            {
                # Written earlier in the batch: no ETag needed
                "op": "update",
                "resource": "pages",
                "id": {"ref": "section"},
                "fields": {"title": "Ein Bereich"},
            },
        ]

    def test_subtree(self):
        response = self.batch(self.subtree())
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        section = Page.objects.get(slug="bereich")
        child = Page.objects.get(slug="unterseite")
        self.assertEqual(data["refs"], {"section": section.pk, "child": child.pk})
        self.assertEqual(section.title, "Ein Bereich")
        self.assertEqual(child.parent, section)
        self.assertEqual(
            list(RichText.objects.filter(parent=child).values_list("text", flat=True)),
            ["<p>A</p>"],
        )
        self.assertEqual(
            [result["fields"]["title"] for result in data["results"]],
            ["Bereich", "Unterseite", "Ein Bereich"],
        )
        # Each operation is in the history as if done on its own
        self.assertEqual(LogEntry.objects.filter(object_id=section.pk).count(), 2)

    def test_dry_run(self):
        count = Page.objects.count()
        response = self.batch(self.subtree(), dry_run="true")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(Page.objects.count(), count)
        self.assertEqual(data["refs"], {"section": None, "child": None})
        child = data["results"][1]
        self.assertIsNone(child["id"])
        self.assertIsNone(child["fields"]["parent"]["id"])
        self.assertIsNone(child["content"]["main"][0]["id"])
        self.assertIsNone(data["results"][2]["id"])

    def test_all_or_nothing(self):
        count = Page.objects.count()
        operations = self.subtree()
        operations[1]["fields"]["title"] = ""
        response = self.batch(operations)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertEqual(data["operation"], 1)
        self.assertIn(
            "operations[1].fields.title", [error["path"] for error in data["errors"]]
        )
        self.assertEqual(Page.objects.count(), count)

    def test_etags(self):
        path = f"pages/{self.angebot.pk}/"
        operation = {
            "op": "update",
            "resource": "pages",
            "id": self.angebot.pk,
            "fields": {"title": "Neu"},
        }
        response = self.batch([operation])
        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.json()["operation"], 0)

        etag = self.etag(path)
        response = self.batch(
            [
                operation | {"etag": etag},
                # The object changed in the batch, the ETag is still the one
                # read before; written earlier, so no ETag is needed.
                {
                    "op": "replace_content",
                    "resource": "pages",
                    "id": self.angebot.pk,
                    "content": {"main": []},
                },
            ]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.angebot.refresh_from_db()
        self.assertEqual(self.angebot.title, "Neu")
        self.assertFalse(RichText.objects.filter(parent=self.angebot).exists())

        response = self.batch([operation | {"etag": etag}])
        self.assertEqual(response.status_code, 412)

    def test_plugin_and_delete(self):
        etag = self.etag(f"pages/{self.angebot.pk}/")
        response = self.batch(
            [
                {
                    "op": "update_plugin",
                    "resource": "pages",
                    "id": self.angebot.pk,
                    "etag": etag,
                    "plugin": {"type": "testapp.button", "id": self.button.pk},
                    "fields": {"button_label": "Los"},
                },
                {
                    "op": "delete",
                    "resource": "pages",
                    "id": self.fr.pk,
                    "etag": self.etag(f"pages/{self.fr.pk}/"),
                },
            ]
        )
        self.assertEqual(response.status_code, 200, response.content)
        results = response.json()["results"]
        self.assertEqual(results[0]["fields"]["button_label"], "Los")
        self.assertIsNone(results[1])
        self.assertEqual(Button.objects.get(pk=self.button.pk).button_label, "Los")
        self.assertFalse(Page.objects.filter(pk=self.fr.pk).exists())

    def test_shape(self):
        response = self.batch(
            [
                {"op": "rename", "resource": "pages"},
                {"op": "update", "resource": "pages", "title": "X"},
                {"op": "create", "resource": "pages", "ref": "a"},
                {"op": "create", "resource": "pages", "ref": "a"},
                {
                    "op": "update_plugin",
                    "resource": "pages",
                    "id": 1,
                    "plugin": 1,
                    "fields": {},
                },
                "delete",
            ]
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            [error["path"] for error in response.json()["errors"]],
            [
                "operations[0].op",
                "operations[1].id",
                "operations[1].title",
                "operations[3].ref",
                "operations[4].plugin",
                "operations[5]",
            ],
        )
        self.assertEqual(self.send("post", "batch/", [1]).status_code, 422)
        self.assertEqual(self.send("post", "batch/", {}).status_code, 422)

    def test_unknown_ref(self):
        response = self.batch(
            [
                {
                    "op": "create",
                    "resource": "pages",
                    "fields": page("X", "x", {"ref": "later"}),
                }
            ]
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["errors"][0]["path"], "operations[0].fields.parent"
        )

    def test_errors_of_operations(self):
        response = self.batch(
            [{"op": "delete", "resource": "pages", "id": 0, "etag": "*"}]
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["operation"], 0)
        response = self.batch([{"op": "delete", "resource": "nothing", "id": 1}])
        self.assertEqual(response.status_code, 404)

    @override_settings(FEINCMS3_API={"MAX_BATCH_OPERATIONS": 2})
    def test_limit(self):
        response = self.batch(self.subtree())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "operations")
