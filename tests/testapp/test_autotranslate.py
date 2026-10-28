import io
import json
import re
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from feincms3_autotranslate import datasets as ds
from feincms3_autotranslate.apps import check_field_settings
from feincms3_autotranslate.backends import TranslationError
from feincms3_autotranslate.fields import field_specs
from feincms3_autotranslate.loader import run_from_dumps, run_from_translated
from feincms3_autotranslate.strings import (
    DumpError,
    check_answer,
    read_dumps,
    split_strings,
)
from feincms3_autotranslate.translate import Job
from feincms3_data.data import dump_specs

from testapp import models as m


def upper_html(text):
    """Upper-case the text between tags, as a translator would change it"""
    return "".join(
        part if part.startswith("<") else part.upper()
        for part in re.split(r"(<[^>]+>)", text)
    )


class Upper:
    model = "upper"

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def translate(
        self, strings, *, source, target, glossary, instructions, title, html_keys
    ):
        self.calls.append(title)
        if any(key in self.fail for key in strings):
            raise TranslationError("Nope.")
        return {key: upper_html(text) for key, text in strings.items()}


def dump(name, argument):
    return json.loads(dump_specs(ds.registry and _datasets()[name]["specs"](argument)))


def _datasets():
    from testapp.datasets import datasets  # noqa: PLC0415

    return datasets()


class Fixture(TestCase):
    def setUp(self):
        self.root_de = m.Page.objects.create(
            title="de", slug="de", path="/de/", static_path=True, language_code="de"
        )
        self.root_fr = m.Page.objects.create(
            title="fr",
            slug="fr",
            path="/fr/",
            static_path=True,
            language_code="fr",
            translation_of=self.root_de,
        )
        self.person = m.Person.objects.create(name="Hans", role_de="Leiter")
        self.form = m.Form.objects.create(
            name="Kontakt", form_type="contact", language_code="de", slot="personal"
        )
        m.Select.objects.create(
            parent=self.form,
            region="form",
            name="answer",
            label="Antwort",
            choices="Ja\nnein|Nein",
            default_value="Ja",
        )
        m.Text.objects.create(
            parent=self.form, region="form", name="name", label="Name", choices=""
        )
        self.tab_list = m.TabList.objects.create(title="Fragen")
        self.angebot = m.Page.objects.create(
            title="Angebot",
            slug="angebot",
            parent=self.root_de,
            language_code="de",
            menu="main",
            lead='<p>Siehe <a href="/de/angebot">Angebot</a></p>',
        )
        self.details = m.Page.objects.create(
            title="Details",
            slug="details",
            parent=self.angebot,
            language_code="de",
            static_path=True,
            path="/details/",
        )
        self.article = m.Article.objects.create(
            title="Neuigkeit", slug="neuigkeit", language_code="de", lead="Lies das"
        )
        self.tag = m.Tag.objects.create(title_de="Neu")
        self.article.tags.add(self.tag)
        m.ArticleButton.objects.create(
            parent=self.article,
            region="main",
            button_label="Zum Angebot",
            button_page=self.angebot,
        )
        m.RichText.objects.create(
            parent=self.angebot,
            region="main",
            text=(
                '<p>Zu den <a href="/details/">Details</a>, zur'
                ' <a href="https://example.com/de/articles/neuigkeit/?x=1#a">News</a>'
                ' und <a href="https://elsewhere.org/">weg</a></p>'
            ),
        )
        m.Button.objects.create(
            parent=self.angebot,
            region="main",
            button_label="Mehr",
            button_page=self.details,
            button_url="",
        )
        m.Button.objects.create(
            parent=self.angebot,
            region="main",
            button_label="Mail",
            button_url="mailto:a@example.com",
            # Not a valid choice (anymore), which isn't the copy's business
            style="stale",
        )
        m.FormContainer.objects.create(
            parent=self.angebot, region="main", form=self.form
        )
        m.TabListElement.objects.create(
            parent=self.angebot, region="main", tab_list=self.tab_list
        )
        m.Tab.objects.create(
            parent=self.tab_list,
            region="de",
            title="Frage",
            text="<p>Antwort</p>",
            button_page=self.details,
        )
        m.Tab.objects.create(parent=self.tab_list, region="it", title="Domanda")
        m.Contact.objects.create(parent=self.angebot, region="main", person=self.person)
        m.KeyFigures.objects.create(
            parent=self.angebot,
            region="main",
            data={
                "title": "Zahlen",
                "figures": [
                    {"title": "Kunden", "number": 3, "unit": "Mio."},
                    {"title": "", "number": 4, "unit": "%"},
                ],
                "button_url": "/de/angebot/",
            },
        )
        m.Anchor.objects.create(
            parent=self.angebot,
            region="main",
            data={"title": "Oben", "id": "oben", "layout": "a"},
        )
        m.PageTeaser.objects.create(
            parent=self.angebot,
            region="main",
            data={"title": "Teaser", "page": str(self.details.pk)},
        )

    def dumps(self, *selections):
        return [dump(*selection.split(":", 1)) for selection in selections]

    def translate(self, dumps, *, target="fr", only=None, translator=None):
        return Job(dumps, target=target, only=only).run(translator or Upper())

    def load(self, translated, **kwargs):
        dry_run = kwargs.pop("dry_run", False)
        run = run_from_translated(translated, **kwargs)
        log = run.load(dry_run=dry_run)
        return run, log


class DatasetTests(Fixture):
    def test_selection(self):
        specs = _datasets()["pages"]["specs"]
        self.assertEqual(specs("Angebot")[0]["filter"], {"pk__in": [self.angebot.pk]})
        self.assertEqual(
            specs("angebot|subtree")[0]["filter"],
            {"pk__in": sorted([self.angebot.pk, self.details.pk])},
        )
        self.assertEqual(
            specs(f"{self.details.pk}")[0]["filter"], {"pk__in": [self.details.pk]}
        )
        # Every page of the language except the root
        self.assertEqual(
            specs("*")[0]["filter"],
            {"pk__in": sorted([self.angebot.pk, self.details.pk])},
        )
        # The tab list comes along, with its source region only
        tab_specs = [s for s in specs("Angebot") if s["model"] == "testapp.tab"]
        self.assertEqual(
            tab_specs[0]["filter"], {"parent__in": [self.tab_list.pk], "region": "de"}
        )

    def test_selection_errors(self):
        specs = _datasets()["pages"]["specs"]
        with self.assertRaisesRegex(CommandError, "roots are never copied"):
            specs("de")
        with self.assertRaisesRegex(CommandError, "(?s)Did you mean.*Angebot"):
            specs("gebo")
        with self.assertRaisesRegex(CommandError, "No .* matches"):
            specs("Angebot|lang=fr")
        with self.assertRaisesRegex(CommandError, "Unknown selection option"):
            specs("Angebot|tree")
        m.Page.objects.create(
            title="Angebot", slug="angebot-2", parent=self.angebot, language_code="de"
        )
        with self.assertRaisesRegex(CommandError, "Several .* match"):
            specs("Angebot")

    def test_language_regions_dataset(self):
        specs = _datasets()["tablists"]["specs"]
        self.assertEqual(specs("*")[0]["filter"], {"pk__in": [self.tab_list.pk]})
        self.assertEqual(specs("*|lang=fr")[0]["filter"], {"pk__in": []})

    def test_object_dataset_requires_offline(self):
        from django.core.exceptions import ImproperlyConfigured  # noqa: PLC0415

        with self.assertRaisesRegex(ImproperlyConfigured, "no is_active"):
            ds.object_dataset(m.Form)


class FieldTests(TestCase):
    def test_fields(self):
        self.assertEqual(
            [str(s) for s in field_specs(m.KeyFigures)],
            ["data.title", "data.figures[].title", "data.button_url"],
        )
        # The anchor's id has a pattern, its layout an enum
        self.assertEqual([s.path for s in field_specs(m.Anchor)], ["data.title"])
        self.assertEqual(
            [str(s) for s in field_specs(m.SimpleField)],
            ["label", "help_text", "choices (labels)", "placeholder"],
        )
        # Fields per language aren't sent
        self.assertEqual(field_specs(m.Person), (field_specs(m.Person)[0],))

    def test_list_fields(self):
        out = io.StringIO()
        call_command("f3autotranslate", list_fields=True, stdout=out)
        self.assertIn(
            "testapp.page\n  title, lead (html)\n  links: redirect_to_url\n",
            out.getvalue(),
        )
        self.assertIn("testapp.simplefield (feincms3-forms)", out.getvalue())

    def test_settings_check(self):
        self.assertEqual(check_field_settings(), [])
        with override_settings(
            FEINCMS3_AUTOTRANSLATE={
                "EXCLUDE_FIELDS": ["testapp.page.titel", "testapp.nope.title"],
                "LINK_FIELDS": ["testapp.jsonplugin_key_figures.data.figures[].nope"],
            }
        ):
            errors = check_field_settings()
        self.assertEqual(len(errors), 3)


class StringTests(TestCase):
    def test_check_answer(self):
        strings = {"a": "<p>Hallo <a href='/x/'>du</a></p>", "b": "Text"}
        self.assertEqual(
            check_answer(
                strings, {"a": "<p>Salut <a href='/x/'>toi</a></p>", "b": "T"}, ["a"]
            ),
            [],
        )
        problems = check_answer(
            strings, {"a": "<p>Salut <a href='/y/'>toi</a></p>"}, ["a"]
        )
        self.assertEqual(
            problems, ["Missing keys: b", "a: the tags differ from the original's"]
        )
        self.assertEqual(check_answer({"b": "x"}, {"b": " "}, []), ["b: empty"])

    def test_regions_send_plugins_only(self):
        tab_list = m.TabList.objects.create(title="Fragen")
        tab = m.Tab.objects.create(parent=tab_list, region="de", title="Frage")
        job = Job([dump("tablists", "Fragen")], target="fr")
        self.assertEqual(
            [strings for _key, _unit, strings, _html in job.wanted()],
            [{f"testapp.tab.{tab.pk}.title": "Frage"}],
        )

    def test_split(self):
        self.assertEqual(
            split_strings({"a": "xx", "b": "xx", "c": "xxxx"}, 4),
            [{"a": "xx", "b": "xx"}, {"c": "xxxx"}],
        )


class RoundTripTests(Fixture):
    def test_round_trip(self):
        dumps = self.dumps("pages:Angebot|subtree", "articles:*", "forms:*")
        plan, summary = run_from_dumps(dumps, target="fr").plan()
        self.assertEqual(
            {item["action"] for item in plan["objects"].values()}, {"create"}
        )
        self.assertIn("To translate: 5 objects", summary)

        translated = self.translate(dumps)
        self.assertEqual(
            {state["status"] for state in translated["units"].values()}, {"translated"}
        )

        # The dry run keeps nothing
        before = m.Page.objects.count()
        run, log = self.load(translated, dry_run=True)
        self.assertTrue(log["dry_run"])
        self.assertNotIn("copy", log["objects"][0])
        self.assertEqual(m.Page.objects.count(), before)
        self.assertFalse(m.Tab.objects.filter(region="fr").exists())

        run, log = self.load(translated)
        report = run.report()

        angebot = m.Page.objects.get(language_code="fr", translation_of=self.angebot)
        details = m.Page.objects.get(language_code="fr", translation_of=self.details)
        self.assertFalse(angebot.is_active)
        self.assertEqual(angebot.title, "ANGEBOT")
        self.assertEqual(angebot.slug, "angebot")
        self.assertEqual(angebot.path, "/fr/angebot/")
        self.assertEqual(angebot.menu, "main")
        self.assertEqual(angebot.parent, self.root_fr)
        # The static path is gone, and reported
        self.assertFalse(details.static_path)
        self.assertEqual(details.path, "/fr/angebot/details/")
        self.assertIn("path=/details/", report)
        # Links in HTML go to the copies
        self.assertEqual(
            angebot.lead, '<p>SIEHE <a href="/fr/angebot/">ANGEBOT</a></p>'
        )
        form = m.Form.objects.get(language_code="fr")
        article = m.Article.objects.get(language_code="fr")
        richtext = m.RichText.objects.get(parent=angebot)
        self.assertEqual(
            richtext.text,
            '<p>ZU DEN <a href="/fr/angebot/details/">DETAILS</a>, ZUR'
            ' <a href="https://example.com/fr/articles/neuigkeit/?x=1#a">NEWS</a>'
            ' UND <a href="https://elsewhere.org/">WEG</a></p>',
        )
        buttons = {b.button_label: b for b in m.Button.objects.filter(parent=angebot)}
        self.assertEqual(buttons["MEHR"].button_page, details)
        self.assertEqual(buttons["MAIL"].button_url, "mailto:a@example.com")
        self.assertEqual(buttons["MAIL"].style, "stale")
        self.assertEqual(m.FormContainer.objects.get(parent=angebot).form, form)
        # The form is offline and keeps its values
        self.assertEqual(form.slot, "")
        self.assertEqual(form.name, "KONTAKT")
        field = m.SimpleField.objects.get(parent=form, name="answer")
        self.assertEqual(field.choices, "ja|JA\nnein|NEIN")
        self.assertEqual((field.name, field.default_value), ("answer", "Ja"))
        # JSON data: strings translated, numbers, ids and links mapped or kept
        figures = m.JSONPlugin.objects.get(parent=angebot, type="key_figures")
        self.assertEqual(
            figures.data,
            {
                "title": "ZAHLEN",
                "figures": [
                    {"title": "KUNDEN", "number": 3, "unit": "Mio."},
                    {"title": "", "number": 4, "unit": "%"},
                ],
                "button_url": "/fr/angebot/",
            },
        )
        anchor = m.JSONPlugin.objects.get(parent=angebot, type="anchor")
        self.assertEqual(anchor.data, {"title": "OBEN", "id": "oben", "layout": "a"})
        teaser = m.JSONPlugin.objects.get(parent=angebot, type="page_teaser")
        self.assertEqual(teaser.data["page"], str(details.pk))
        # The article points at the French page, keeps its tag
        self.assertFalse(article.is_active)
        self.assertEqual(article.slug, "neuigkeit")
        self.assertEqual(
            m.ArticleButton.objects.get(parent=article).button_page, angebot
        )
        self.assertEqual(list(article.tags.all()), [self.tag])
        # The tab list got a French region, pointing at the French page
        tab = m.Tab.objects.get(region="fr")
        self.assertEqual(
            (tab.parent, tab.title, tab.button_page), (self.tab_list, "FRAGE", details)
        )
        self.assertEqual(m.Tab.objects.filter(region="it").count(), 1)
        self.assertEqual(
            m.TabListElement.objects.get(parent=angebot).tab_list, self.tab_list
        )
        # Originals are untouched
        self.angebot.refresh_from_db()
        self.assertEqual(self.angebot.title, "Angebot")
        self.assertEqual(m.Tab.objects.get(region="de").title, "Frage")
        # The person stays German and its role has no French text
        self.assertEqual(m.Contact.objects.get(parent=angebot).person, self.person)
        self.assertIn("role has no fr text", report)
        self.assertIn("title has no fr text", report)  # the tag

        # The log names the copies
        copies = {(o["model"], o["original"]): o for o in log["objects"]}
        self.assertEqual(copies[("testapp.page", self.angebot.pk)]["copy"], angebot.pk)
        self.assertEqual(copies[("testapp.page", self.angebot.pk)]["status"], "created")
        self.assertEqual(copies[("testapp.tablist", self.tab_list.pk)]["region"], "fr")

        # Running again skips everything, except the form, whose model has
        # no translation_of to recognise a copy by
        plan, _summary = run_from_dumps(dumps, target="fr").plan()
        self.assertEqual(
            {
                k: o["action"]
                for k, o in plan["objects"].items()
                if o["action"] != "skip"
            },
            {f"testapp.form.{self.form.pk}": "create"},
        )
        dumps = self.dumps("pages:Angebot|subtree", "articles:*")
        run, log = self.load(self.translate(dumps))
        self.assertEqual({o["status"] for o in log["objects"]}, {"skipped"})
        self.assertEqual(m.Page.objects.filter(language_code="fr").count(), 3)

        # A page added afterwards is the only one to translate, and goes
        # under the existing translation
        new = m.Page.objects.create(
            title="Neu", slug="neu", parent=self.angebot, language_code="de"
        )
        dumps = self.dumps("pages:Angebot|subtree")
        plan, summary = run_from_dumps(dumps, target="fr").plan()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(plan))
            from feincms3_autotranslate.translate import read_plan  # noqa: PLC0415

            only = read_plan(path, target="fr")
        self.assertEqual(only, {f"testapp.page.{new.pk}"})
        translator = Upper()
        translated = self.translate(dumps, only=only, translator=translator)
        self.assertEqual(translator.calls, ["testapp.page Neu"])
        run, log = self.load(translated)
        copy = m.Page.objects.get(translation_of=new)
        self.assertEqual(copy.parent, angebot)
        self.assertEqual(copy.path, "/fr/angebot/neu/")

        # --replace replaces the content, not the slug and the activation
        angebot.slug = "offre"
        angebot.is_active = True
        angebot.save()
        m.RichText.objects.create(parent=angebot, region="main", text="<p>Hand</p>")
        dumps = self.dumps("pages:Angebot")
        plan, _summary = run_from_dumps(dumps, target="fr", replace=True).plan()
        # The tab list's French region is replaced too
        self.assertEqual(
            [o["action"] for o in plan["objects"].values()], ["replace", "replace"]
        )
        run, log = self.load(self.translate(dumps), replace=True)
        angebot.refresh_from_db()
        self.assertEqual(
            (angebot.slug, angebot.is_active, angebot.title), ("offre", True, "ANGEBOT")
        )
        self.assertEqual(m.RichText.objects.filter(parent=angebot).count(), 1)
        self.assertEqual(log["objects"][0]["status"], "replaced")

    def test_regions_go_live(self):
        dumps = self.dumps("pages:Angebot|subtree")
        run, _log = self.load(self.translate(dumps))
        # Activate the French page using the tab list, then translate the
        # tab list again into its region
        m.Page.objects.filter(language_code="fr").update(is_active=True)
        dumps = self.dumps("tablists:Fragen")
        run, _log = self.load(self.translate(dumps), replace=True)
        report = run.report()
        self.assertTrue(report.startswith("GOES LIVE AT ONCE"), report)
        self.assertEqual(m.Tab.objects.filter(region="fr").count(), 1)

    def test_failed_object(self):
        dumps = self.dumps("pages:Angebot|subtree")
        translated = self.translate(
            dumps, translator=Upper(fail={f"testapp.page.{self.angebot.pk}.title"})
        )
        self.assertEqual(
            translated["units"][f"testapp.page.{self.angebot.pk}"],
            {"status": "failed", "reason": "Nope."},
        )
        run, log = self.load(translated)
        self.assertEqual(
            {
                o["original"]: o["status"]
                for o in log["objects"]
                if o["model"] == "testapp.page"
            },
            {self.angebot.pk: "failed", self.details.pk: "failed"},
        )
        self.assertIn("FAILED:", run.report())
        self.assertFalse(
            m.Page.objects.filter(language_code="fr", parent__isnull=False).exists()
        )
        # The tab list doesn't depend on the page and is loaded nevertheless,
        # its tab's button stays with the German page.
        self.assertEqual(m.Tab.objects.get(region="fr").button_page, self.details)

    def test_parent_needs_counterpart(self):
        dumps = self.dumps("pages:Details")
        with self.assertRaisesRegex(CommandError, "Translate that page first"):
            run_from_dumps(dumps, target="fr").plan()
        _run, _log = self.load(self.translate(dumps), parent=self.root_fr.pk)
        self.assertEqual(
            m.Page.objects.get(translation_of=self.details).path, "/fr/details/"
        )

    def test_same_language(self):
        with self.assertRaisesRegex(CommandError, "Copying without translating"):
            run_from_dumps(self.dumps("pages:Angebot"), target="de")

    def test_slug_suffix(self):
        m.Article.objects.create(title="Other", slug="neuigkeit", language_code="fr")
        run, _log = self.load(self.translate(self.dumps("articles:*")))
        self.assertEqual(
            m.Article.objects.get(translation_of=self.article).slug, "neuigkeit-2"
        )
        self.assertIn("'neuigkeit-2'", run.report())

    def test_application_page(self):
        app_de = m.Page.objects.create(
            title="News",
            slug="news",
            parent=self.root_de,
            language_code="de",
            page_type="articles",
        )
        m.Page.objects.create(
            title="Actualités",
            slug="actualites",
            parent=self.root_fr,
            language_code="fr",
            page_type="articles",
        )
        plan, _summary = run_from_dumps(self.dumps("pages:News"), target="fr").plan()
        self.assertEqual(plan["objects"][f"testapp.page.{app_de.pk}"]["action"], "skip")
        self.assertIn(
            "application page", plan["objects"][f"testapp.page.{app_de.pk}"]["reason"]
        )


class NoTranslationOfTests(Fixture):
    def setUp(self):
        super().setUp()
        self.training = m.Training.objects.create(
            title="Kurs",
            slug="kurs",
            language_code="de",
            contents='<p><a href="/de/angebot/">Angebot</a></p>',
        )
        m.Speaker.objects.create(training=self.training, person=self.person)
        m.Session.objects.create(
            training=self.training,
            title="Morgen",
            subscription_url="https://example.org/",
        )

    def test_twice_and_into(self):
        dumps = self.dumps("trainings:Kurs")
        run, log = self.load(self.translate(dumps))
        copy = m.Training.objects.get(language_code="fr")
        self.assertEqual(
            (copy.title, copy.slug, copy.is_active), ("KURS", "kurs", False)
        )
        self.assertEqual(copy.speaker_set.get().person, self.person)
        self.assertEqual(copy.session_set.get().title, "MORGEN")
        # The page isn't translated, so the link stays German and is listed
        self.assertIn("/de/angebot/", copy.contents)
        report = run.report()
        self.assertIn("has no translation_of", report)
        self.assertIn("no unique slug either", report)
        self.assertIn("References left in another language than fr", report)

        # Running again creates a second copy...
        self.load(self.translate(dumps))
        self.assertEqual(m.Training.objects.filter(language_code="fr").count(), 2)
        # ... unless --into names the earlier one
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "load.json"
            path.write_text(json.dumps(log))
            from feincms3_autotranslate.loader import parse_into  # noqa: PLC0415

            into = parse_into([str(path)])
        self.assertEqual(into, {("testapp.training", self.training.pk): copy.pk})
        run, log = self.load(self.translate(dumps), into=into, replace=True)
        self.assertEqual(log["objects"][0]["status"], "replaced")
        self.assertEqual(m.Training.objects.filter(language_code="fr").count(), 2)
        self.assertEqual(copy.session_set.count(), 1)


class CommandTests(Fixture):
    def test_prepare_apply_load(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            out = io.StringIO()
            call_command("f3dumpdata", "articles:*", stdout=out)
            (tmp / "articles.json").write_text(out.getvalue())

            out, err = io.StringIO(), io.StringIO()
            call_command(
                "f3autotranslateload",
                str(tmp / "articles.json"),
                plan=True,
                target="fr",
                stdout=out,
                stderr=err,
            )
            (tmp / "plan.json").write_text(out.getvalue())
            self.assertIn("To translate: 1 objects", err.getvalue())

            call_command(
                "f3autotranslate",
                str(tmp / "articles.json"),
                target="fr",
                only=str(tmp / "plan.json"),
                prepare=str(tmp / "strings.json"),
                stderr=io.StringIO(),
            )
            strings = json.loads((tmp / "strings.json").read_text())
            self.assertIn("prévoyance", strings["rules"])
            self.assertIn("Never translate these terms: Acme", strings["rules"])
            for item in strings["objects"].values():
                item["strings"] = {k: v.upper() for k, v in item["strings"].items()}
            (tmp / "strings.fr.json").write_text(json.dumps(strings))

            call_command(
                "f3autotranslate",
                str(tmp / "articles.json"),
                target="fr",
                apply=[str(tmp / "strings.fr.json")],
                output=str(tmp / "fr.json"),
                review=str(tmp / "review.html"),
                stderr=io.StringIO(),
            )
            review = (tmp / "review.html").read_text()
            self.assertIn("NEUIGKEIT", review)
            self.assertIn("Lies das", review)

            with self.assertRaisesRegex(DumpError, "translated file, not a dump"):
                read_dumps([tmp / "fr.json"])

            out, err = io.StringIO(), io.StringIO()
            call_command(
                "f3autotranslateload",
                str(tmp / "fr.json"),
                dry_run=True,
                stdout=out,
                stderr=err,
            )
            self.assertTrue(json.loads(out.getvalue())["dry_run"])
            self.assertIn("DRY RUN", err.getvalue())
            self.assertIn("To create:", err.getvalue())
            self.assertFalse(m.Article.objects.filter(language_code="fr").exists())

            out = io.StringIO()
            call_command(
                "f3autotranslateload",
                str(tmp / "fr.json"),
                stdout=out,
                stderr=io.StringIO(),
            )
            log = json.loads(out.getvalue())
            copy = m.Article.objects.get(language_code="fr")
            self.assertEqual(log["objects"][0]["copy"], copy.pk)
            self.assertEqual(copy.lead, "LIES DAS")

    def test_prepare_in_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "pages.json").write_text(
                json.dumps(dump("pages", "Angebot|subtree"))
            )
            call_command(
                "f3autotranslate",
                str(tmp / "pages.json"),
                target="fr",
                prepare=str(tmp / "strings.json"),
                max_chars=10,
                stderr=io.StringIO(),
            )
            parts = sorted(tmp.glob("strings-*.json"))
            self.assertEqual(len(parts), 3)  # Angebot, Details, the tab list
            for part in parts:
                data = json.loads(part.read_text())
                self.assertIn("rules", data)
                for item in data["objects"].values():
                    item["strings"] = {
                        k: upper_html(v) for k, v in item["strings"].items()
                    }
                part.with_suffix(".fr.json").write_text(json.dumps(data))
            call_command(
                "f3autotranslate",
                str(tmp / "pages.json"),
                target="fr",
                apply=[str(p.with_suffix(".fr.json")) for p in parts],
                output=str(tmp / "fr.json"),
                stderr=io.StringIO(),
            )
            translated = json.loads((tmp / "fr.json").read_text())
            self.assertEqual(
                {s["status"] for s in translated["units"].values()}, {"translated"}
            )

    def test_resume(self):
        dumps = self.dumps("pages:Angebot|subtree")
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "fr.json.partial"
            job = Job(dumps, target="fr")
            job.run(Upper(), partial=partial)
            lines = partial.read_text().splitlines()
            # Pretend the run was interrupted after the first object
            partial.write_text(lines[0] + "\n" + lines[1][:10])
            translator = Upper()
            translated = Job(dumps, target="fr").run(
                translator, partial=partial, resume=True
            )
            self.assertEqual(len(translator.calls), len(lines) - 1)
            self.assertEqual(
                {s["status"] for s in translated["units"].values()}, {"translated"}
            )

    def test_backend_requires_model(self):
        with (
            override_settings(
                FEINCMS3_AUTOTRANSLATE={
                    "BACKEND": "feincms3_autotranslate.backends.anthropic.Anthropic"
                }
            ),
            tempfile.TemporaryDirectory() as tmp,
        ):
            path = Path(tmp) / "a.json"
            path.write_text(json.dumps(dump("articles", "*")))
            with self.assertRaisesRegex(CommandError, "no default"):
                call_command(
                    "f3autotranslate",
                    str(path),
                    target="fr",
                    output=str(Path(tmp) / "fr.json"),
                )

    def test_overlapping_dumps(self):
        # The tab list comes with the pages and on its own
        dumps = self.dumps("pages:Angebot|subtree", "tablists:Fragen")
        _run, log = self.load(self.translate(dumps))
        self.assertEqual(
            [o["status"] for o in log["objects"] if o["model"] == "testapp.tablist"],
            ["created"],
        )
        dumps[1]["objects"][0]["fields"]["title"] = "Changed"
        with self.assertRaisesRegex(CommandError, "differs between the dumps"):
            run_from_dumps(dumps, target="fr")

    def test_foreign_dump(self):
        dumps = [
            {
                "version": 1,
                "specs": [],
                "objects": [
                    {"model": "testapp.person", "pk": 1, "fields": {"name": "x"}}
                ],
            }
        ]
        with self.assertRaisesRegex(CommandError, "no autotranslate dataset"):
            run_from_dumps(dumps, target="fr")


class AnthropicTests(TestCase):
    def backend(self, *answers):
        from unittest import mock  # noqa: PLC0415

        from feincms3_autotranslate.backends.anthropic import Anthropic  # noqa: PLC0415

        backend = Anthropic(model="some-model")
        messages = iter(answers)
        requests = []

        class Stream:
            def __init__(self, **kwargs):
                requests.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_final_message(self):
                stop_reason, text = next(messages)
                return mock.Mock(
                    stop_reason=stop_reason,
                    content=[mock.Mock(type="text", text=text)],
                )

        backend.client = mock.Mock()
        backend.client.messages.stream = Stream
        return backend, requests

    def translate(self, backend, strings):
        return backend.translate(
            strings,
            source="de",
            target="fr",
            glossary={"keep": ["Acme"], "terms": {"BVG": "LPP"}},
            instructions="Vous.",
            title="testapp.page Angebot",
            html_keys=["b"],
        )

    def test_split_and_fence(self):
        backend, requests = self.backend(
            ("end_turn", '```json\n{"a": "A"}\n```'), ("end_turn", '{"b": "<p>B</p>"}')
        )
        with override_settings(FEINCMS3_AUTOTRANSLATE={"MAX_REQUEST_CHARS": 5}):
            answer = self.translate(backend, {"a": "aaaa", "b": "<p>b</p>"})
        self.assertEqual(answer, {"a": "A", "b": "<p>B</p>"})
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["model"], "some-model")
        self.assertIn("BVG -> LPP", requests[0]["system"])
        self.assertIn("Vous.", requests[0]["system"])
        self.assertIn("testapp.page Angebot", requests[1]["messages"][0]["content"])
        self.assertIn("HTML values: b", requests[1]["messages"][0]["content"])

    def test_failures(self):
        for answer, message in [
            (("max_tokens", '{"a": "A"'), "cut off"),
            (("refusal", ""), "declined"),
            (("end_turn", "Voilà"), "isn't valid JSON"),
            (("end_turn", "[1]"), "isn't a JSON object"),
        ]:
            backend, _requests = self.backend(answer)
            with self.assertRaisesRegex(TranslationError, message):
                self.translate(backend, {"a": "a"})
