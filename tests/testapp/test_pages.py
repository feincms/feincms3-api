from testapp.models import Page
from testapp.test_write import WriteTestCase


class PagesTest(WriteTestCase):
    def find(self, resource="pages", **params):
        return self.client.get(f"/api/{resource}/find/", params)

    def test_find(self):
        response = self.find(path="/de/angebot/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], self.angebot.pk)
        self.assertEqual(response["ETag"], response.json()["meta"]["etag"])

        self.assertEqual(self.find(path="/de/nichts/").status_code, 404)
        self.assertEqual(self.find().status_code, 404)
        # Only pages
        self.assertEqual(self.find("articles", path="/de/").status_code, 404)

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
        path = f"pages/{self.angebot.pk}/"
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

    def test_translations(self):
        self.fr_angebot = Page.objects.create(
            title="Offre",
            slug="offre",
            parent=self.fr,
            language_code="fr",
            page_type="standard",
            translation_of=self.angebot,
        )
        data = self.get("pages/", translation_of=self.angebot.pk).json()
        self.assertEqual([item["id"] for item in data["items"]], [self.fr_angebot.pk])
