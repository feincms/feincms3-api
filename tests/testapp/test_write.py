import json

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission, User
from django.core.cache import cache

from testapp.models import Accordion, Article, Contact, Page, Person, RichText
from testapp.test_api import APITestCase


class WriteTestCase(APITestCase):
    def send(self, method, path, data=None, *, etag=None, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        headers = {"If-Match": etag} if etag else {}
        return getattr(self.client, method)(
            f"/api/{path}" + (f"?{query}" if query else ""),
            json.dumps(data) if data is not None else None,
            content_type="application/json",
            headers=headers,
        )

    def etag(self, path):
        return self.get(path)["ETag"]


class CreateTest(WriteTestCase):
    def test_create_page_with_content(self):
        response = self.send(
            "post",
            "pages/",
            {
                "fields": {
                    "title": "Kontakt",
                    "slug": "kontakt",
                    "parent": {"type": "testapp.page", "id": self.home.pk},
                    "page_type": "standard",
                    "language_code": "de",
                    "is_active": False,
                },
                "content": {
                    "main": [
                        {"type": "testapp.richtext", "fields": {"text": "<p>Hi</p>"}},
                        {"type": "testapp.accordion", "fields": {"title": "FAQ"}},
                        {
                            "type": "testapp.richtext",
                            "depth": 1,
                            "fields": {"text": "<p>Antwort</p>"},
                        },
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        page = Page.objects.get(pk=data["id"])
        self.assertEqual(page.path, "/de/kontakt/")
        self.assertIs(page.is_active, False)
        self.assertEqual(response["ETag"], data["meta"]["etag"])
        self.assertEqual(
            [(p["type"], p["depth"]) for p in data["content"]["main"]],
            [
                ("testapp.richtext", 0),
                ("testapp.accordion", 0),
                ("testapp.richtext", 1),
            ],
        )
        self.assertEqual(
            list(RichText.objects.filter(parent=page).values_list("ordering", "depth")),
            [(10, 0), (30, 1)],
        )
        entry = LogEntry.objects.get(object_id=str(page.pk))
        self.assertTrue(entry.is_addition())
        self.assertIn("(Through the API.)", entry.change_message)

    def test_create_dry_run(self):
        response = self.send(
            "post",
            "articles/",
            {
                "fields": {"title": "News", "slug": "news", "language_code": "de"},
                "content": {
                    "main": [
                        {"type": "testapp.articlerichtext", "fields": {"text": "x"}}
                    ]
                },
            },
            dry_run="true",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIsNone(data["id"])
        self.assertIsNone(data["content"]["main"][0]["id"])
        self.assertFalse(Article.objects.exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_create_errors(self):
        response = self.send(
            "post",
            "pages/",
            {
                "fields": {
                    "slug": "x",
                    "language_code": "de",
                    "page_type": "standard",
                    "unknown": 1,
                    "path": "/x/",
                },
                "content": {
                    "main": [
                        {"type": "testapp.richtext", "depth": 1, "fields": {}},
                        {"type": "testapp.jsonplugin_anchor", "fields": {}},
                        {"type": "testapp.nothing"},
                        {"type": "testapp.button", "fields": {"button_page": "x"}},
                    ],
                    "sidebar": [],
                },
            },
        )
        self.assertEqual(response.status_code, 422)
        errors = {(e["path"], e["message"]) for e in response.json()["errors"]}
        paths = {path for path, _message in errors}
        self.assertIn(("fields.unknown", "Unknown field."), errors)
        self.assertIn(("fields.path", "Read-only."), errors)
        self.assertIn("fields.title", paths)
        self.assertIn("content.main[0].depth", paths)
        self.assertIn("content.main[0].fields.text", paths)
        self.assertIn(
            (
                "content.main[1].type",
                "testapp.jsonplugin_anchor isn't allowed in region main.",
            ),
            errors,
        )
        self.assertIn("content.main[2].type", paths)
        self.assertIn("content.main[3].fields.button_label", paths)
        self.assertIn("content.main[3].fields.button_page", paths)
        self.assertIn("content.sidebar", paths)
        self.assertEqual(Page.objects.count(), 3)

    def test_permission(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_page", content_type__app_label="testapp"
            )
        )
        self.client.force_login(user)
        response = self.send("post", "pages/", {"fields": {"title": "x"}})
        self.assertEqual(response.status_code, 403)


class UpdateTest(WriteTestCase):
    def test_etag_required(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send("patch", path, {"fields": {"title": "Neu"}})
        self.assertEqual(response.status_code, 428)

        response = self.send("patch", path, {"fields": {"title": "Neu"}}, etag='"x"')
        self.assertEqual(response.status_code, 412)
        self.assertEqual(response.json()["etag"], self.etag(path))

        response = self.send("patch", path, {"fields": {"title": "Neu"}}, etag="*")
        self.assertEqual(response.status_code, 200)

    def test_update_fields(self):
        path = f"pages/{self.angebot.pk}/"
        etag = self.etag(path)
        response = self.send(
            "patch",
            path,
            {"fields": {"title": "Unser Angebot", "lead": "<p>Neu</p>"}},
            etag=etag,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.angebot.refresh_from_db()
        self.assertEqual(self.angebot.title, "Unser Angebot")
        self.assertEqual(self.angebot.slug, "angebot")  # Unchanged
        self.assertNotEqual(response["ETag"], etag)
        # The content is untouched
        self.assertEqual(len(response.json()["content"]["main"]), 6)
        entry = LogEntry.objects.get()
        self.assertTrue(entry.is_change())

        # The old ETag doesn't work anymore
        response = self.send("patch", path, {"fields": {"title": "x"}}, etag=etag)
        self.assertEqual(response.status_code, 412)

    def test_round_trip(self):
        """What was read can be written back, read-only fields included"""
        path = f"pages/{self.angebot.pk}/"
        response = self.get(path)
        data = response.json()
        response = self.send(
            "patch",
            path,
            {"fields": data["fields"], "content": data["content"]},
            etag=response["ETag"],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["content"], data["content"])
        self.assertEqual(response["ETag"], data["meta"]["etag"])

    def test_replace_content(self):
        path = f"pages/{self.angebot.pk}/"
        etag = self.etag(path)
        response = self.send(
            "put",
            f"{path}content/",
            {
                "content": {
                    "main": [
                        {
                            "type": "testapp.button",
                            "id": self.button.pk,
                            "fields": {"button_label": "Weiter"},
                        },
                        {
                            "type": "testapp.richtext",
                            "id": self.text.pk,
                            "fields": {"text": "<p>Hallo Welt</p>"},
                        },
                        {"type": "testapp.richtext", "fields": {"text": "<p>Neu</p>"}},
                    ]
                }
            },
            etag=etag,
        )
        self.assertEqual(response.status_code, 200, response.content)
        main = response.json()["content"]["main"]
        self.assertEqual(
            [plugin["type"] for plugin in main],
            ["testapp.button", "testapp.richtext", "testapp.richtext"],
        )
        self.button.refresh_from_db()
        self.assertEqual(self.button.button_label, "Weiter")
        self.assertEqual(self.button.ordering, 10)
        self.assertEqual(self.button.button_page, self.home)  # Kept
        self.text.refresh_from_db()
        self.assertEqual(
            (self.text.text, self.text.ordering), ("<p>Hallo Welt</p>", 20)
        )
        # Left out, so deleted
        self.assertFalse(Accordion.objects.exists())
        self.assertFalse(RichText.objects.filter(pk=self.inner.pk).exists())

        message = LogEntry.objects.get().get_change_message()
        self.assertIn("Accordion", message)
        self.assertTrue(message.endswith("(Through the API.)"))

    def test_replace_content_dry_run(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send(
            "put",
            f"{path}content/",
            {
                "content": {
                    "main": [{"type": "testapp.richtext", "fields": {"text": "x"}}]
                }
            },
            etag=self.etag(path),
            dry_run="true",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["content"]["main"][0]["id"], None)
        self.assertTrue(Accordion.objects.exists())
        self.assertEqual(RichText.objects.filter(parent=self.angebot).count(), 2)

    def test_excluded_json_values_are_kept(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send(
            "put",
            f"{path}content/",
            {
                "content": {
                    "main": [
                        {
                            "type": "testapp.jsonplugin_key_figures",
                            "id": self.figures.pk,
                            "fields": {
                                "data": {
                                    "title": "Zahlen",
                                    "figures": [{"title": "Ertrag", "number": 4}],
                                }
                            },
                        }
                    ]
                }
            },
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.figures.refresh_from_db()
        self.assertEqual(
            self.figures.data["figures"],
            [{"title": "Ertrag", "number": 4, "unit": "%"}],
        )

    def test_plugin_of_another_object(self):
        other = RichText.objects.create(parent=self.home, region="main", text="x")
        path = f"pages/{self.angebot.pk}/"
        response = self.send(
            "put",
            f"{path}content/",
            {"content": {"main": [{"type": "testapp.richtext", "id": other.pk}]}},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "content.main[0].id")

    def test_invalid_page_type(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send(
            "patch",
            path,
            {"fields": {"page_type": "nothing"}},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "fields.page_type")


class PluginTest(WriteTestCase):
    def test_update_plugin(self):
        path = f"pages/{self.angebot.pk}/"
        etag = self.etag(path)
        response = self.send(
            "patch",
            f"{path}plugins/testapp.button/{self.button.pk}/",
            {"fields": {"button_label": "Los", "button_page": None}},
            etag=etag,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fields"]["button_label"], "Los")
        self.assertNotEqual(response["ETag"], etag)
        self.assertEqual(response["ETag"], self.etag(path))
        self.button.refresh_from_db()
        self.assertEqual(
            (self.button.button_label, self.button.button_page), ("Los", None)
        )
        self.assertEqual(self.button.ordering, 40)  # Unchanged

        response = self.send(
            "patch",
            f"{path}plugins/testapp.button/{self.button.pk}/",
            {"fields": {"button_label": ""}},
            etag=response["ETag"],
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "fields.button_label")

    def test_json_schema_validation(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send(
            "patch",
            f"{path}plugins/testapp.jsonplugin_key_figures/{self.figures.pk}/",
            {"fields": {"data": {"title": 3}}},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["errors"][0]["path"], "fields.data")


class DeleteTest(WriteTestCase):
    def test_delete(self):
        path = f"pages/{self.angebot.pk}/"
        response = self.send("delete", path, etag=self.etag(path), dry_run="true")
        self.assertEqual(response.status_code, 204)
        self.assertTrue(Page.objects.filter(pk=self.angebot.pk).exists())

        response = self.send("delete", path, etag=self.etag(path))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Page.objects.filter(pk=self.angebot.pk).exists())
        self.assertTrue(LogEntry.objects.get().is_deletion())

    def test_page_with_children(self):
        path = f"pages/{self.home.pk}/"
        response = self.send("delete", path, etag=self.etag(path))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["children"][0]["id"], self.angebot.pk)

    def test_protected(self):
        person = Person.objects.create(name="Anna")
        Contact.objects.create(parent=self.angebot, region="main", person=person)
        path = f"persons/{person.pk}/"
        response = self.send("delete", path, etag=self.etag(path))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["protected"][0]["type"], "testapp.contact")
        self.assertTrue(Person.objects.filter(pk=person.pk).exists())


class CacheTest(WriteTestCase):
    def test_clear_cache(self):
        key = f"regions-testapp.page-{self.angebot.pk}"
        cache.set(key, "rendered")
        response = self.send("delete", f"pages/{self.angebot.pk}/cache/")
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(cache.get(key))

        cache.set(key, "rendered")
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_page", content_type__app_label="testapp"
            )
        )
        self.client.force_login(user)
        response = self.send("delete", f"pages/{self.angebot.pk}/cache/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(cache.get(key), "rendered")
