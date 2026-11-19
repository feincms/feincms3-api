from cabinet.models import File, Folder
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile

from testapp.models import Contact, PageTeaser, Person, Speaker, Training
from testapp.test_api import APITestCase
from testapp.test_cabinet import png


class ReferencesTest(APITestCase):
    def setUp(self):
        super().setUp()
        folder = Folder.objects.create(name="Leute")
        self.portrait = File(folder=folder, image_alt_text="Anna lacht")
        self.portrait.file = SimpleUploadedFile("anna.png", png())
        self.portrait.save()
        self.person = Person.objects.create(name="Anna", portrait=self.portrait)
        Contact.objects.create(
            parent=self.angebot, region="main", ordering=70, person=self.person
        )
        # In JSON data, as bare ids: one which exists, one which doesn't
        PageTeaser.objects.create(
            parent=self.angebot,
            region="main",
            ordering=80,
            data={"title": "Weiter", "page": str(self.fr.pk)},
        )
        PageTeaser.objects.create(
            parent=self.angebot,
            region="main",
            ordering=90,
            data={"title": "Weg", "page": "999999"},
        )
        self.path = f"resources/pages/{self.angebot.pk}/"

    def test_detail(self):
        plain = self.get(self.path).json()
        self.assertNotIn("references", plain["meta"])

        data = self.get(self.path, expand="references").json()
        self.assertEqual(data["meta"]["etag"], plain["meta"]["etag"])
        # References stay what they are
        self.assertEqual(data["fields"], plain["fields"])
        self.assertEqual(data["content"], plain["content"])

        references = data["meta"]["references"]
        self.assertEqual(
            sorted(references["testapp.page"]),
            sorted([str(self.home.pk), str(self.fr.pk), "999999"]),
        )
        home = references["testapp.page"][str(self.home.pk)]
        self.assertEqual(home["title"], "Startseite")
        self.assertEqual(home["path"], "/de/")
        self.assertEqual(home["url"], "http://testserver/de/")
        self.assertTrue(home["admin_url"].endswith(f"/page/{self.home.pk}/change/"))
        self.assertIsNone(references["testapp.page"]["999999"])
        self.assertEqual(
            references["testapp.person"][str(self.person.pk)]["title"], "Anna"
        )

    def test_files_and_inlines(self):
        data = self.get(
            f"resources/persons/{self.person.pk}/", expand="references"
        ).json()
        portrait = data["meta"]["references"]["cabinet.file"][str(self.portrait.pk)]
        self.assertEqual(portrait["name"], "anna.png")
        self.assertEqual((portrait["width"], portrait["height"]), (40, 20))
        self.assertEqual(portrait["alt_text"], "Anna lacht")
        self.assertTrue(portrait["url"].startswith("http://testserver/"))

        training = Training.objects.create(
            title="Kurs", slug="kurs", language_code="de"
        )
        Speaker.objects.create(training=training, person=self.person)
        data = self.get(
            f"resources/trainings/{training.pk}/", expand="references"
        ).json()
        self.assertEqual(
            data["meta"]["references"]["testapp.person"][str(self.person.pk)]["title"],
            "Anna",
        )

    def test_list_find_and_plugin(self):
        data = self.get(
            "resources/pages/", expand="references", content="true", language_code="de"
        ).json()
        by_id = {item["id"]: item for item in data["items"]}
        self.assertIn("testapp.person", by_id[self.angebot.pk]["meta"]["references"])
        # Each item has only its own
        self.assertEqual(by_id[self.home.pk]["meta"]["references"], {})

        data = self.client.get(
            "/api/resources/pages/find/",
            {"path": "/de/angebot/", "expand": "references"},
        ).json()
        self.assertIn("testapp.person", data["meta"]["references"])

        data = self.get(
            f"{self.path}plugins/testapp.button/{self.button.pk}/",
            expand="references",
        ).json()
        self.assertEqual(list(data["meta"]["references"]), ["testapp.page"])

    def test_only_what_the_user_may_see(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="testapp",
                codename__in=["view_page", "view_contact"],
            )
        )
        self.client.force_login(user)
        # The contact plugin is visible, the person it refers to isn't
        references = self.get(self.path, expand="references").json()["meta"][
            "references"
        ]
        self.assertEqual(references["testapp.person"], {str(self.person.pk): None})
        self.assertIsNotNone(references["testapp.page"][str(self.home.pk)])
