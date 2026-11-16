"""The admin's other inlines, and its hooks around saving"""

from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission, User

from testapp.admin import ArticleAdmin
from testapp.models import Article, ArticleRichText, Person, Session, Speaker, Training
from testapp.test_write import WriteTestCase


class InlinesTest(WriteTestCase):
    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(name="Anna")
        self.training = Training.objects.create(
            title="Kurs", slug="kurs", language_code="de"
        )
        self.speaker = Speaker.objects.create(
            training=self.training, person=self.person
        )
        self.morning = Session.objects.create(training=self.training, title="Morgen")
        self.evening = Session.objects.create(training=self.training, title="Abend")
        self.path = f"trainings/{self.training.pk}/"

    def test_read(self):
        data = self.get(self.path).json()
        self.assertEqual(
            data["inlines"],
            {
                "testapp.speaker": [
                    {
                        "id": self.speaker.pk,
                        "fields": {
                            "person": {"type": "testapp.person", "id": self.person.pk}
                        },
                    }
                ],
                "testapp.session": [
                    {
                        "id": self.morning.pk,
                        "fields": {"title": "Morgen", "subscription_url": ""},
                    },
                    {
                        "id": self.evening.pk,
                        "fields": {"title": "Abend", "subscription_url": ""},
                    },
                ],
            },
        )
        self.assertNotIn("content", data)

        schema = self.get("schema/").json()
        training = next(r for r in schema["resources"] if r["name"] == "trainings")
        self.assertEqual(training["inlines"], ["testapp.speaker", "testapp.session"])
        self.assertEqual(
            schema["types"]["testapp.session"]["inline_of"], "testapp.training"
        )
        self.assertEqual(self.get("schema/testapp.session/").status_code, 200)

    def test_replace(self):
        etag = self.etag(self.path)
        response = self.send(
            "patch",
            self.path,
            {
                "inlines": {
                    "testapp.session": [
                        {"id": self.evening.pk, "fields": {"title": "Nacht"}},
                        {"fields": {"title": "Mittag"}},
                    ]
                }
            },
            etag=etag,
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertNotEqual(data["meta"]["etag"], etag)
        sessions = {s.title: s for s in Session.objects.filter(training=self.training)}
        self.assertEqual(set(sessions), {"Nacht", "Mittag"})
        # The project's save_formset() ran
        self.assertEqual(
            sessions["Mittag"].subscription_url, "https://example.com/kurs/"
        )
        # It fills in changed sessions too
        self.assertEqual(
            sessions["Nacht"].subscription_url, "https://example.com/kurs/"
        )
        # Not given, left alone
        self.assertTrue(Speaker.objects.filter(pk=self.speaker.pk).exists())

        message = LogEntry.objects.get().get_change_message()
        self.assertIn("Mittag", message)
        self.assertIn("Morgen", message)
        self.assertIn("(Through the API.)", message)

    def test_create_with_inlines(self):
        # The test settings hide the training's slug, which is required
        with self.settings(FEINCMS3_API={}):
            response = self.send(
                "post",
                "trainings/",
                {
                    "fields": {"title": "Neu", "slug": "neu", "language_code": "de"},
                    "inlines": {
                        "testapp.speaker": [{"fields": {"person": self.person.pk}}],
                        "testapp.session": [{"fields": {"title": "Eins"}}],
                    },
                },
            )
        self.assertEqual(response.status_code, 201, response.content)
        training = Training.objects.get(pk=response.json()["id"])
        self.assertEqual(training.speaker_set.get().person, self.person)
        self.assertEqual(
            training.session_set.get().subscription_url, "https://example.com/neu/"
        )

    def test_dry_run(self):
        response = self.send(
            "patch",
            self.path,
            {"inlines": {"testapp.session": [{"fields": {"title": "Neu"}}]}},
            etag="*",
            dry_run="true",
        )
        self.assertEqual(response.status_code, 200, response.content)
        [session] = response.json()["inlines"]["testapp.session"]
        self.assertIsNone(session["id"])
        self.assertEqual(Session.objects.filter(training=self.training).count(), 2)

    def test_errors(self):
        response = self.send(
            "patch",
            self.path,
            {
                "inlines": {
                    "testapp.session": [
                        {"id": self.morning.pk},
                        {"id": self.evening.pk, "fields": {"title": "Morgen"}},
                        {"fields": {"title": ""}},
                        {"id": 0},
                        {"fields": {}, "type": "x"},
                    ],
                    "testapp.richtext": [],
                    "testapp.nothing": [],
                }
            },
            etag="*",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            sorted(error["path"] for error in response.json()["errors"]),
            [
                "inlines.testapp.nothing",
                "inlines.testapp.richtext",
                # The formset's own clean(), titles twice
                "inlines.testapp.session",
                "inlines.testapp.session[2].fields.title",
                "inlines.testapp.session[3].id",
                "inlines.testapp.session[4].type",
            ],
        )

    def test_permissions(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="testapp",
                codename__in=[
                    "view_training",
                    "change_training",
                    "view_session",
                    "change_session",
                    "view_speaker",
                ],
            )
        )
        self.client.force_login(user)
        response = self.send(
            "patch",
            self.path,
            {
                "inlines": {
                    "testapp.session": [
                        {"id": self.morning.pk},
                        {"fields": {"title": "X"}},
                    ]
                }
            },
            etag="*",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            sorted(error["path"] for error in response.json()["errors"]),
            ["inlines.testapp.session", "inlines.testapp.session[1]"],
        )

        # Changing the training leaves the view-only speakers alone
        response = self.send(
            "patch", self.path, {"fields": {"title": "Kurs 2"}}, etag="*"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Speaker.objects.filter(pk=self.speaker.pk).exists())


class HooksTest(WriteTestCase):
    def test_save_related(self):
        article = Article.objects.create(title="News", slug="news", language_code="de")
        path = f"articles/{article.pk}/"
        writes = [
            ("patch", path, {"fields": {"title": "Neu"}}),
            (
                "put",
                f"{path}content/",
                {
                    "content": {
                        "main": [
                            {"type": "testapp.articlerichtext", "fields": {"text": "x"}}
                        ]
                    }
                },
            ),
        ]
        with mock.patch.object(
            ArticleAdmin,
            "save_related",
            autospec=True,
            side_effect=ArticleAdmin.save_related,
        ) as save_related:
            for method, url, data in writes:
                response = self.send(method, url, data, etag="*")
                self.assertEqual(response.status_code, 200, response.content)
            plugin = ArticleRichText.objects.get(parent=article)
            response = self.send(
                "patch",
                f"{path}plugins/testapp.articlerichtext/{plugin.pk}/",
                {"fields": {"text": "<p>y</p>"}},
                etag="*",
            )
            self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(save_related.call_count, 3)
        # As in the admin: a formset per inline, here the plugins
        _admin, _request, _form, formsets, change = save_related.call_args.args
        self.assertTrue(change)
        self.assertEqual(
            [formset.model for formset in formsets],
            [inline.model for inline in ArticleAdmin.inlines],
        )
        plugin.refresh_from_db()
        self.assertEqual(plugin.text, "<p>y</p>")
