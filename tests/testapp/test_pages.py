from django.contrib.admin.models import LogEntry

from testapp.models import Page
from testapp.test_write import WriteTestCase


class PagesTest(WriteTestCase):
    def find(self, resource="testapp.page", **params):
        return self.client.get(f"/api/resources/{resource}/find/", params)

    def test_find(self):
        response = self.find(path="/de/angebot/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], self.angebot.pk)
        self.assertEqual(response["ETag"], response.json()["meta"]["etag"])

        self.assertEqual(self.find(path="/de/nichts/").status_code, 404)
        self.assertEqual(self.find().status_code, 404)
        # Only pages
        self.assertEqual(self.find("testapp.article", path="/de/").status_code, 404)

    def test_move(self):
        about = Page.objects.create(
            title="Über uns",
            slug="ueber-uns",
            parent=self.home,
            language_code="de",
            page_type="standard",
        )
        child = Page.objects.create(
            title="Details",
            slug="details",
            parent=self.angebot,
            language_code="de",
            page_type="standard",
        )
        path = f"resources/testapp.page/{self.angebot.pk}/"
        response = self.send(
            "patch",
            path,
            {"fields": {"parent": {"type": "testapp.page", "id": about.pk}}},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["path"], "/de/ueber-uns/angebot/")
        child.refresh_from_db()
        self.assertEqual(child.path, "/de/ueber-uns/angebot/details/")

        # Not below itself
        response = self.send(
            "patch",
            path,
            {"fields": {"parent": child.pk}},
            etag=self.etag(path),
        )
        # feincms3 says so in the model's clean(), for the object as a whole
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "fields")

    def children(self, parent):
        data = self.get("resources/testapp.page/", parent=parent.pk).json()
        return [item["id"] for item in data["items"]]

    def test_move_operation(self):
        about = Page.objects.create(
            title="Über uns",
            slug="ueber-uns",
            parent=self.home,
            language_code="de",
            page_type="standard",
        )
        child = Page.objects.create(
            title="Details",
            slug="details",
            parent=self.angebot,
            language_code="de",
            page_type="standard",
        )
        self.assertEqual(self.children(self.home), [self.angebot.pk, about.pk])
        schema = self.get("schema/").json()
        pages = next(r for r in schema["resources"] if r["name"] == "testapp.page")
        self.assertEqual(
            pages["move_positions"],
            ["before", "first-child", "last-child", "after", "root"],
        )
        path = f"resources/testapp.page/{about.pk}/"
        move = f"{path}move/"
        body = {"position": "before", "relative_to": self.angebot.pk}

        self.assertEqual(self.send("post", move, body).status_code, 428)
        response = self.send("post", move, body, etag=self.etag(path), dry_run="true")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.children(self.home), [self.angebot.pk, about.pk])

        response = self.send("post", move, body, etag=self.etag(path))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["ETag"], response.json()["meta"]["etag"])
        self.assertEqual(self.children(self.home), [about.pk, self.angebot.pk])
        self.assertIn(
            "Moved (before",
            LogEntry.objects.filter(object_id=str(about.pk))
            .latest("pk")
            .change_message,
        )

        # Below another page: the paths of the subtree follow
        path = f"resources/testapp.page/{self.angebot.pk}/"
        response = self.send(
            "post",
            f"{path}move/",
            {"position": "last-child", "relative_to": about.pk},
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["path"], "/de/ueber-uns/angebot/")
        child.refresh_from_db()
        self.assertEqual(child.path, "/de/ueber-uns/angebot/details/")

        # Not below itself, not without a node to be relative to, only the
        # admin's positions
        for body, error_path in [
            ({"position": "first-child", "relative_to": child.pk}, "relative_to"),
            ({"position": "after"}, ""),
            ({"position": "sideways", "relative_to": about.pk}, "position"),
            ({"relative_to": about.pk}, "position"),
            ({"position": "root", "where": 1}, "where"),
        ]:
            response = self.send("post", f"{path}move/", body, etag="*")
            self.assertEqual(response.status_code, 422, body)
            self.assertEqual(response.json()["errors"][0]["path"], error_path, body)

        # To the roots, last
        response = self.send("post", f"{path}move/", {"position": "root"}, etag="*")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fields"]["parent"], None)

    def test_move_in_batch(self):
        response = self.send(
            "post",
            "batch/",
            {
                "operations": [
                    {
                        "op": "create",
                        "resource": "testapp.page",
                        "ref": "b",
                        "fields": self.page_fields("b"),
                    },
                    {
                        "op": "create",
                        "resource": "testapp.page",
                        "ref": "a",
                        "fields": self.page_fields("a"),
                    },
                    {
                        "op": "move",
                        "resource": "testapp.page",
                        "id": {"ref": "a"},
                        "position": "first-child",
                        "relative_to": self.home.pk,
                    },
                    # Renumbered by the move, but written in this batch
                    {
                        "op": "update",
                        "resource": "testapp.page",
                        "id": self.angebot.pk,
                        "etag": self.etag(f"resources/testapp.page/{self.angebot.pk}/"),
                        "fields": {"title": "Angebote"},
                    },
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        refs = response.json()["refs"]
        self.assertEqual(
            self.children(self.home), [refs["a"], self.angebot.pk, refs["b"]]
        )

    def page_fields(self, slug):
        return {
            "title": slug,
            "slug": slug,
            "parent": self.home.pk,
            "language_code": "de",
            "page_type": "standard",
            "is_active": False,
        }

    def test_translations(self):
        self.fr_angebot = Page.objects.create(
            title="Offre",
            slug="offre",
            parent=self.fr,
            language_code="fr",
            page_type="standard",
            translation_of=self.angebot,
        )
        data = self.get(
            "resources/testapp.page/", translation_of=self.angebot.pk
        ).json()
        self.assertEqual([item["id"] for item in data["items"]], [self.fr_angebot.pk])

    def test_path_clash(self):
        response = self.send(
            "post",
            "resources/testapp.page/",
            {
                "fields": {
                    "title": "Angebot",
                    "slug": "angebot",
                    "parent": self.home.pk,
                    "language_code": "de",
                    "page_type": "standard",
                    "is_active": True,
                }
            },
        )
        self.assertEqual(response.status_code, 422, response.content)
        # Reported by feincms3; the form has no path field
        self.assertEqual(response.json()["errors"][0]["path"], "fields")
        self.assertIn("/de/angebot/", response.json()["errors"][0]["message"])
