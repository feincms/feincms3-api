import io
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from testapp.models import (
    Accordion,
    Article,
    ArticleRichText,
    Button,
    Download,
    KeyFigures,
    Page,
    RichText,
    Tab,
    TabList,
)


SNAPSHOT = Path(__file__).parent / "snapshot.txt"


class APITestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp()
        cls.settings_override = override_settings(MEDIA_ROOT=cls.media)
        cls.settings_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.settings_override.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.superuser = User.objects.create_superuser("admin", "", "admin")
        self.client.force_login(self.superuser)

        self.home = Page.objects.create(
            title="Startseite",
            slug="de",
            language_code="de",
            page_type="standard",
            is_active=True,
            lead="<p>Willkommen</p>",
        )
        self.angebot = Page.objects.create(
            title="Angebot",
            slug="angebot",
            parent=self.home,
            language_code="de",
            page_type="standard",
            is_active=False,
        )
        self.fr = Page.objects.create(
            title="Accueil", slug="fr", language_code="fr", page_type="standard"
        )

        self.text = RichText.objects.create(
            parent=self.angebot, region="main", ordering=10, text="<p>Hallo</p>"
        )
        self.accordion = Accordion.objects.create(
            parent=self.angebot, region="main", ordering=20, title="Fragen"
        )
        self.inner = RichText.objects.create(
            parent=self.angebot,
            region="main",
            ordering=30,
            depth=1,
            text="<p>Antwort</p>",
        )
        self.button = Button.objects.create(
            parent=self.angebot,
            region="main",
            ordering=40,
            button_label="Mehr",
            button_page=self.home,
        )
        self.figures = KeyFigures.objects.create(
            parent=self.angebot,
            region="main",
            ordering=50,
            data={
                "title": "Zahlen",
                "figures": [{"title": "Rendite", "number": 3, "unit": "%"}],
            },
        )
        self.download = Download.objects.create(
            parent=self.angebot,
            region="main",
            ordering=60,
            file=SimpleUploadedFile("preise.pdf", b"%PDF-1.4"),
        )

    def get(self, path, **params):
        return self.client.get(f"/api/{path}", params)


class ReadTest(APITestCase):
    def test_list(self):
        response = self.get("pages/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(
            {item["fields"]["title"] for item in data["items"]},
            {"Startseite", "Angebot", "Accueil"},
        )
        self.assertNotIn("content", data["items"][0])

    def test_list_filters_and_paging(self):
        data = self.get("pages/", language_code="de").json()
        self.assertEqual(data["count"], 2)

        data = self.get("pages/", is_active="false").json()
        self.assertEqual([item["id"] for item in data["items"]], [self.angebot.pk])

        data = self.get("pages/", parent=self.home.pk).json()
        self.assertEqual([item["id"] for item in data["items"]], [self.angebot.pk])

        data = self.get("pages/", parent="null").json()
        self.assertEqual(data["count"], 2)

        data = self.get("pages/", q="angeb").json()
        self.assertEqual([item["id"] for item in data["items"]], [self.angebot.pk])

        data = self.get("pages/", limit=1, offset=1).json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["items"]), 1)

        data = self.get("pages/", fields="title,slug").json()
        self.assertEqual(set(data["items"][0]["fields"]), {"title", "slug"})

        data = self.get("pages/", content="true", parent=self.home.pk).json()
        self.assertEqual(len(data["items"][0]["content"]["main"]), 6)

    def test_detail(self):
        response = self.get(f"pages/{self.angebot.pk}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["type"], "testapp.page")
        self.assertEqual(data["id"], self.angebot.pk)
        fields = data["fields"]
        self.assertEqual(fields["title"], "Angebot")
        self.assertEqual(fields["parent"], {"type": "testapp.page", "id": self.home.pk})
        self.assertEqual(fields["page_type"], "standard")
        self.assertIs(fields["is_active"], False)
        self.assertEqual(fields["path"], "/de/angebot/")

        meta = data["meta"]
        self.assertEqual(meta["path"], "/de/angebot/")
        self.assertEqual(meta["title"], "Angebot")
        self.assertEqual(
            meta["admin_url"], f"/admin/testapp/page/{self.angebot.pk}/change/"
        )
        self.assertEqual(
            meta["regions"], [{"key": "main", "title": "Main", "inherited": False}]
        )
        self.assertEqual(meta["warnings"], [])
        self.assertEqual(response["ETag"], meta["etag"])

        main = data["content"]["main"]
        self.assertEqual(
            [(plugin["type"], plugin["depth"]) for plugin in main],
            [
                ("testapp.richtext", 0),
                ("testapp.accordion", 0),
                ("testapp.richtext", 1),
                ("testapp.button", 0),
                ("testapp.jsonplugin_key_figures", 0),
                ("testapp.download", 0),
            ],
        )
        self.assertEqual(
            main[0],
            {
                "type": "testapp.richtext",
                "id": self.text.pk,
                "depth": 0,
                "fields": {"text": "<p>Hallo</p>"},
            },
        )
        self.assertEqual(main[1]["fields"], {"title": "Fragen"})
        self.assertEqual(main[2]["id"], self.inner.pk)
        main = main[:2] + main[3:]
        self.assertEqual(
            main[2]["fields"],
            {
                "button_label": "Mehr",
                "style": "",
                "button_url": "",
                "button_page": {"type": "testapp.page", "id": self.home.pk},
            },
        )
        # The unit is excluded in the settings
        self.assertEqual(
            main[3]["fields"],
            {
                "data": {
                    "title": "Zahlen",
                    "figures": [{"title": "Rendite", "number": 3}],
                }
            },
        )
        file = main[4]["fields"]["file"]
        self.assertTrue(file["name"].startswith("downloads/preise"))
        self.assertEqual(file["url"], f"/media/{file['name']}")

    def test_nested(self):
        response = self.get(f"pages/{self.angebot.pk}/", nested="true")
        main = response.json()["content"]["main"]
        self.assertEqual(len(main), 5)
        # The container holds the plugin with the greater depth
        self.assertEqual(
            main[1],
            {
                "type": "testapp.accordion",
                "id": self.accordion.pk,
                "fields": {"title": "Fragen"},
                "children": [
                    {
                        "type": "testapp.richtext",
                        "id": self.inner.pk,
                        "fields": {"text": "<p>Antwort</p>"},
                    }
                ],
            },
        )
        # Same object, same ETag
        self.assertEqual(
            response["ETag"], self.get(f"pages/{self.angebot.pk}/")["ETag"]
        )

    def test_etag_changes(self):
        etag = self.get(f"pages/{self.angebot.pk}/")["ETag"]
        self.assertEqual(self.get(f"pages/{self.angebot.pk}/")["ETag"], etag)
        self.inner.text = "<p>Andere Antwort</p>"
        self.inner.save()
        self.assertNotEqual(self.get(f"pages/{self.angebot.pk}/")["ETag"], etag)

    def test_regions_per_language_and_excluded_fields(self):
        tabs = TabList.objects.create(title="Tabs")
        Tab.objects.create(parent=tabs, region="de", ordering=10, title="Eins")
        data = self.get(f"tab-lists/{tabs.pk}/").json()
        self.assertEqual(
            data["content"],
            {
                "de": [
                    {
                        "type": "testapp.tab",
                        "id": tabs.testapp_tab_set.get().pk,
                        "depth": 0,
                        "fields": {"title": "Eins", "text": "", "button_page": None},
                    }
                ],
                "fr": [],
                "it": [],
            },
        )

    def test_plugin(self):
        data = self.get(
            f"pages/{self.angebot.pk}/plugins/testapp.jsonplugin_key_figures/{self.figures.pk}/"
        ).json()
        self.assertEqual(data["region"], "main")
        self.assertEqual(data["fields"]["data"]["title"], "Zahlen")

        # Another page's plugin, another type, a missing one
        for path in [
            f"pages/{self.home.pk}/plugins/testapp.richtext/{self.text.pk}/",
            f"pages/{self.angebot.pk}/plugins/testapp.button/99999/",
            f"pages/{self.angebot.pk}/plugins/testapp.richtext/0/",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)

    def test_not_found(self):
        self.assertEqual(self.get("pages/0/").status_code, 404)
        self.assertEqual(self.get("pages/abc/").status_code, 404)
        self.assertEqual(self.get("nothing/").status_code, 404)

    def test_wrong_region(self):
        RichText.objects.create(parent=self.angebot, region="sidebar", text="<p>x</p>")
        data = self.get(f"pages/{self.angebot.pk}/").json()
        self.assertEqual(len(data["content"]["sidebar"]), 1)
        warning = data["meta"]["warnings"][0]
        self.assertIn("'sidebar'", warning["message"])
        self.assertEqual(warning["path"], "content.sidebar")

    def test_article(self):
        article = Article.objects.create(title="News", slug="news", language_code="de")
        ArticleRichText.objects.create(parent=article, region="main", text="<p>a</p>")
        data = self.get(f"articles/{article.pk}/").json()
        self.assertEqual(data["meta"]["url"], "http://testserver/de/articles/news/")
        self.assertEqual(data["content"]["main"][0]["fields"], {"text": "<p>a</p>"})


class SchemaTest(APITestCase):
    def test_schema(self):
        data = self.get("schema/").json()
        pages = next(r for r in data["resources"] if r["name"] == "pages")
        standard = pages["region_sets"][0]
        self.assertEqual(standard["key"], "standard")
        main = standard["regions"][0]
        self.assertEqual(main["key"], "main")
        self.assertIn("testapp.jsonplugin_key_figures", main["plugins"])
        # deny_regions({"main"}) allows it nowhere
        self.assertNotIn("testapp.jsonplugin_anchor", main["plugins"])
        self.assertIs(data["types"]["testapp.accordion"]["container"], True)
        self.assertEqual(data["types"]["testapp.richtext"]["plugin_of"], "testapp.page")

    def test_type_schema(self):
        data = self.get("schema/testapp.jsonplugin_key_figures/").json()
        figure = data["properties"]["data"]["properties"]["figures"]["items"]
        self.assertEqual(figure["properties"]["title"]["x-feincms3-kind"], "text")
        self.assertNotIn("unit", figure["properties"])

        data = self.get("schema/testapp.page/").json()
        properties = data["properties"]
        self.assertEqual(properties["lead"]["x-feincms3-kind"], "html")
        self.assertEqual(properties["slug"]["x-feincms3-kind"], "machine")
        self.assertEqual(properties["redirect_to_url"]["x-feincms3-kind"], "link")
        self.assertIs(properties["path"]["readOnly"], True)
        self.assertEqual(properties["parent"]["x-feincms3-ref"], "testapp.page")
        self.assertIn("title", data["required"])

        self.assertEqual(self.get("schema/testapp.nothing/").status_code, 404)

    def test_allowed_html(self):
        # From the prose-editor field's extensions
        text = self.get("schema/testapp.articlerichtext/").json()["properties"]["text"]
        self.assertEqual(
            text["x-feincms3-html"],
            {
                "tags": ["a", "h2", "h3", "p", "strong"],
                "attributes": {"a": ["href", "rel", "target", "title"]},
                "url_schemes": ["https", "mailto"],
                "sanitized": True,
            },
        )
        # Legacy mode, nothing known
        text = self.get("schema/testapp.richtext/").json()["properties"]["text"]
        self.assertNotIn("x-feincms3-html", text)

    def test_snapshot(self):
        out = io.StringIO()
        call_command("f3api_schema", "testapp.content_api.content_api", stdout=out)
        self.assertEqual(out.getvalue(), SNAPSHOT.read_text())


class PermissionTest(APITestCase):
    def test_anonymous(self):
        self.client.logout()
        self.assertEqual(self.get("pages/").status_code, 401)
        self.assertEqual(self.get("schema/").status_code, 401)

    def test_not_staff(self):
        user = User.objects.create_user("user", "", "user")
        self.client.force_login(user)
        self.assertEqual(self.get("pages/").status_code, 401)

    def test_staff_without_permissions(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.get("pages/").status_code, 403)
        self.assertEqual(self.get(f"pages/{self.home.pk}/").status_code, 403)
        self.assertEqual(self.get("schema/").json()["resources"], [])

    def test_staff_with_page_permissions_only(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                codename="view_page", content_type__app_label="testapp"
            )
        )
        self.client.force_login(user)
        data = self.get(f"pages/{self.angebot.pk}/").json()
        # Viewing only: the fields are there, read-only
        self.assertEqual(data["fields"]["title"], "Angebot")
        schema = self.get("schema/testapp.page/").json()
        self.assertIs(schema["properties"]["title"]["readOnly"], True)
        # No permissions for the plugins: none of them are shown
        self.assertNotIn("content", data)
        self.assertEqual(self.get("articles/").status_code, 403)
