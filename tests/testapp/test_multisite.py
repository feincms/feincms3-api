import json

from django.contrib.auth.models import User
from django.test import TestCase
from feincms3_sites.models import Site
from langapp.models import Page as LanguagePage
from sitesapp.models import Page as SitePage


class MultisiteTestCase(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "", "admin"))

    def get(self, url, **params):
        return self.client.get(f"/api/multisite/{url}", params)

    def post(self, path, data):
        return self.client.post(
            f"/api/multisite/{path}", json.dumps(data), content_type="application/json"
        )


class SitesTest(MultisiteTestCase):
    """feincms3-sites: paths are unique per site"""

    def setUp(self):
        super().setUp()
        self.com = Site.objects.create(host="example.com", is_default=True)
        self.org = Site.objects.create(host="example.org")
        self.home_com = SitePage.objects.create(
            title="Home", slug="home", site=self.com, page_type="standard"
        )
        self.home_org = SitePage.objects.create(
            title="Home", slug="home", site=self.org, page_type="standard"
        )

    def test_find(self):
        response = self.get("site-pages/find/", path="/home/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(response.json()["pages"]), 2)

        response = self.get("site-pages/find/", path="/home/", host="example.org")
        self.assertEqual(response.json()["id"], self.home_org.pk)
        # The default site serves unknown hosts, as feincms3-sites does
        response = self.get("site-pages/find/", path="/home/", host="example.net")
        self.assertEqual(response.json()["id"], self.home_com.pk)

    def test_create(self):
        # The same path on the other site is fine
        SitePage.objects.create(
            title="Kontakt", slug="kontakt", parent=self.home_com, page_type="standard"
        )
        response = self.post(
            "site-pages/",
            {
                "fields": {
                    "title": "Kontakt",
                    "slug": "kontakt",
                    "parent": self.home_org.pk,
                    "page_type": "standard",
                },
                "content": {
                    "main": [{"type": "sitesapp.richtext", "fields": {"text": "Hi"}}]
                },
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        page = SitePage.objects.get(pk=response.json()["id"])
        self.assertEqual((page.site, page.path), (self.org, "/home/kontakt/"))

        # A second one on the same site clashes
        response = self.post(
            "site-pages/",
            {
                "fields": {
                    "title": "Kontakt",
                    "slug": "kontakt",
                    "parent": self.home_org.pk,
                    "page_type": "standard",
                }
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "fields.path")

    def test_root_needs_a_site(self):
        response = self.post(
            "site-pages/",
            {"fields": {"title": "Start", "slug": "start", "page_type": "standard"}},
        )
        self.assertEqual(response.status_code, 422)
        response = self.post(
            "site-pages/",
            {
                "fields": {
                    "title": "Start",
                    "slug": "start",
                    "page_type": "standard",
                    "site": {"type": "feincms3_sites.site", "id": self.org.pk},
                }
            },
        )
        self.assertEqual(response.status_code, 201, response.content)


class LanguageSitesTest(MultisiteTestCase):
    """feincms3-language-sites: paths are unique per language, one host each"""

    def setUp(self):
        super().setUp()
        self.de = LanguagePage.objects.create(
            title="Start",
            slug="start",
            language_code="de",
            static_path=True,
            path="/",
            page_type="standard",
        )
        self.fr = LanguagePage.objects.create(
            title="Accueil",
            slug="accueil",
            language_code="fr",
            static_path=True,
            path="/",
            page_type="standard",
            translation_of=self.de,
        )

    def test_find(self):
        response = self.get("language-pages/find/", path="/")
        self.assertEqual(response.status_code, 409)

        response = self.get("language-pages/find/", path="/", host="example.fr")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], self.fr.pk)
        self.assertTrue(
            data["meta"]["url"].endswith("//example.fr/"), data["meta"]["url"]
        )

        response = self.get("language-pages/find/", path="/", host="example.ch")
        self.assertEqual(response.status_code, 404)

    def test_create(self):
        response = self.post(
            "language-pages/",
            {
                "fields": {
                    "title": "Offre",
                    "slug": "offre",
                    "parent": self.fr.pk,
                    "language_code": "fr",
                    "page_type": "standard",
                }
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["meta"]["path"], "/offre/")
        self.assertTrue(response.json()["meta"]["url"].endswith("//example.fr/offre/"))

        data = self.get("language-pages/", translation_of=self.de.pk).json()
        self.assertEqual([item["id"] for item in data["items"]], [self.fr.pk])
