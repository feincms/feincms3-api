import contextlib
import importlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest import mock

import click
from django.contrib.auth.models import User
from django.test import LiveServerTestCase, SimpleTestCase, override_settings

from feincms3_api.models import APIToken
from testapp.models import Button, Page


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from feincms3_api_cli import skills as cli_skills
from feincms3_api_cli.config import prog
from feincms3_api_cli.main import main


CLI = Path(__file__).resolve().parents[2] / "cli"


class Terminal(io.StringIO):
    def isatty(self):
        return True


class CLITest(LiveServerTestCase):
    # The translate skill's example uses fixed ids
    reset_sequences = True

    def setUp(self):
        user = User.objects.create_superuser("admin", "", "admin")
        _token, self.secret = APIToken.objects.create_token(
            user=user, name="cli", access=APIToken.Access.WRITE
        )
        self.home = Page.objects.create(
            title="Startseite",
            slug="de",
            language_code="de",
            page_type="standard",
            is_active=True,
        )
        self.button = Button.objects.create(
            parent=self.home, region="main", ordering=10, button_label="Los"
        )
        self.dirs = tempfile.TemporaryDirectory()
        self.addCleanup(self.dirs.cleanup)
        media = override_settings(MEDIA_ROOT=f"{self.dirs.name}/media")
        media.enable()
        self.addCleanup(media.disable)
        env = mock.patch.dict(
            os.environ,
            {
                "F3API_URL": f"{self.live_server_url}/api/",
                "F3API_TOKEN": self.secret,
                "F3API_CONFIG": f"{self.dirs.name}/config",
                "F3API_CACHE": f"{self.dirs.name}/cache",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *args, stdin="", tty=False):
        stdout, stderr = (Terminal() if tty else io.StringIO()), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            mock.patch("sys.stdin", io.StringIO(stdin)),
        ):
            code = main([str(arg) for arg in args])
        out = stdout.getvalue()
        if tty:
            return code, out, stderr.getvalue()
        return code, json.loads(out) if out.strip() else None, stderr.getvalue()

    def test_read(self):
        code, data, _err = self.run_cli("resources")
        self.assertEqual(code, 0)
        self.assertIn("pages", [r["name"] for r in data])
        code, data, _err = self.run_cli("list", "pages", "-f", "language_code=de")
        self.assertEqual(data["count"], 1)
        code, data, _err = self.run_cli("find", "/de/")
        self.assertEqual(data["id"], self.home.pk)
        code, data, err = self.run_cli("get", "pages", 0)
        self.assertEqual(code, 5)
        self.assertIn("404", err)
        code, data, err = self.run_cli("list", "page")
        self.assertEqual(code, 5)
        self.assertIn("No resource 'page'. The resources: pages,", err)

    def test_tables(self):
        code, out, err = self.run_cli("list", "pages", tty=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Startseite", out)
        self.assertIn("/de/", out)
        self.assertIn("1–1 of 1", out)
        code, out, _err = self.run_cli("resources", tty=True)
        self.assertIn("testapp.page", out)
        # JSON when asked for
        code, out, _err = self.run_cli("list", "pages", "--json", tty=True)
        self.assertEqual(json.loads(out)["count"], 1)

    def test_usage(self):
        code, _data, err = self.run_cli("get", "pages")
        self.assertEqual(code, 2)
        self.assertIn("Missing argument", err)
        code, out, _err = self.run_cli("--help", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("Exit codes", out)

    def test_update_with_etags(self):
        page = f"{self.home.pk}"
        body = '{"fields": {"title": "Neu"}}'
        # Not read yet: no ETag
        code, _data, err = self.run_cli("update", "pages", page, stdin=body)
        self.assertEqual(code, 4)
        self.assertIn("read it first", err)

        self.run_cli("get", "pages", page)
        # A dry run by default
        code, data, err = self.run_cli("update", "pages", page, stdin=body)
        self.assertEqual(code, 0, err)
        self.assertEqual(data["fields"]["title"], "Neu")
        self.assertIn("dry run", err)
        self.home.refresh_from_db()
        self.assertEqual(self.home.title, "Startseite")

        code, _data, err = self.run_cli("update", "pages", page, "--apply", stdin=body)
        self.assertEqual(code, 0, err)
        self.home.refresh_from_db()
        self.assertEqual(self.home.title, "Neu")

        # The new ETag was remembered, so writing again works
        code, _data, err = self.run_cli(
            "update-plugin",
            "pages",
            page,
            "testapp.button",
            self.button.pk,
            "--apply",
            stdin='{"button_label": "Weiter"}',
        )
        self.assertEqual(code, 0, err)
        self.button.refresh_from_db()
        self.assertEqual(self.button.button_label, "Weiter")

        # Someone else changes the page: the remembered ETag is outdated
        Page.objects.filter(pk=self.home.pk).update(title="Anders")
        code, _data, err = self.run_cli("update", "pages", page, "--apply", stdin=body)
        self.assertEqual(code, 4)
        self.assertIn("412", err)

    def test_errors_with_paths(self):
        code, _data, err = self.run_cli(
            "create", "pages", stdin='{"fields": {"title": ""}}'
        )
        self.assertEqual(code, 3)
        self.assertIn("fields.title:", err)
        self.assertIn("fields.is_active:", err)

    def test_batch(self):
        operations = [
            {
                "op": "create",
                "resource": "pages",
                "ref": "a",
                "fields": {
                    "title": "Neu",
                    "slug": "neu",
                    "parent": self.home.pk,
                    "page_type": "standard",
                    "language_code": "de",
                    "is_active": False,
                },
            },
            {
                "op": "update",
                "resource": "pages",
                "id": {"ref": "a"},
                "fields": {"title": "Neuer"},
            },
        ]
        code, data, err = self.run_cli("batch", "--apply", stdin=json.dumps(operations))
        self.assertEqual(code, 0, err)
        page = Page.objects.get(slug="neu")
        self.assertEqual(data["refs"], {"a": page.pk})
        self.assertEqual(page.title, "Neuer")
        # Remembered: can be updated without reading it again
        code, _data, err = self.run_cli(
            "update", "pages", page.pk, stdin='{"fields": {"title": "X"}}'
        )
        self.assertEqual(code, 0, err)

    def test_upload(self):
        path = Path(self.dirs.name) / "liste.txt"
        path.write_text("Hallo")
        code, data, err = self.run_cli("upload", path)
        self.assertEqual(code, 0, err)
        self.assertEqual(data["name"], "liste.txt")
        self.assertIn("upload", data)

    def test_sites(self):
        with mock.patch.dict(os.environ, {"F3API_URL": ""}):
            code, _data, _err = self.run_cli(
                "site",
                "add",
                "here",
                f"{self.live_server_url}/api/",
                "--token-stdin",
                stdin=self.secret,
            )
            self.assertEqual(code, 0)
            code, data, _err = self.run_cli("site", "list")
            self.assertEqual(data["here"]["default"], True)
            config = Path(self.dirs.name) / "config" / "sites.json"
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            with mock.patch.dict(os.environ, {"F3API_TOKEN": ""}):
                code, data, err = self.run_cli("list", "pages")
            self.assertEqual(code, 0, err)
            self.assertEqual(
                (Path(self.dirs.name) / "config").stat().st_mode & 0o777, 0o700
            )

        # A site named with --site wins over F3API_URL and F3API_TOKEN
        with mock.patch.dict(
            os.environ, {"F3API_URL": "http://127.0.0.1:1/", "F3API_TOKEN": "x"}
        ):
            code, data, err = self.run_cli("--site", "here", "list", "pages")
        self.assertEqual(code, 0, err)

    def test_transport_errors(self):
        with mock.patch.dict(os.environ, {"F3API_URL": "http://127.0.0.1:1/"}):
            code, _data, err = self.run_cli("list", "pages")
        self.assertEqual(code, 1)
        self.assertIn("Can't connect", err)
        # The admin redirects to its login: not followed
        with mock.patch.dict(
            os.environ, {"F3API_URL": f"{self.live_server_url}/admin/"}
        ):
            code, _data, err = self.run_cli("list", "pages")
        self.assertEqual(code, 1)
        self.assertIn("redirected to", err)
        # Not the API: no HTML error pages
        with mock.patch.dict(
            os.environ, {"F3API_URL": f"{self.live_server_url}/nothing/"}
        ):
            code, _data, err = self.run_cli("list", "pages")
        self.assertEqual(code, 5)
        self.assertIn("404 Not Found (http", err)
        self.assertNotIn("<", err)

    def test_batch_delete_ref(self):
        operations = [
            {
                "op": "create",
                "resource": "pages",
                "ref": "a",
                "fields": {
                    "title": "Weg",
                    "slug": "weg",
                    "parent": self.home.pk,
                    "page_type": "standard",
                    "language_code": "de",
                    "is_active": False,
                },
            },
            {"op": "delete", "resource": "pages", "id": {"ref": "a"}},
        ]
        code, data, err = self.run_cli("batch", "--apply", stdin=json.dumps(operations))
        self.assertEqual(code, 0, err)
        self.assertFalse(Page.objects.filter(slug="weg").exists())
        cache = json.loads((Path(self.dirs.name) / "cache" / "etags.json").read_text())
        self.assertFalse(any(key.endswith(f"/{data['refs']['a']}") for key in cache))

    def test_content_wrapped_or_not(self):
        self.run_cli("get", "pages", self.home.pk)
        regions = {
            "main": [{"type": "testapp.richtext", "fields": {"text": "<p>A</p>"}}]
        }
        for body in [regions, {"content": regions}]:
            code, data, err = self.run_cli(
                "content", "pages", self.home.pk, stdin=json.dumps(body)
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(
                [plugin["type"] for plugin in data["content"]["main"]],
                ["testapp.richtext"],
            )

    def test_translate_skill_batch_example(self):
        """The skill's batch example passes a dry run"""
        text = (
            CLI / "feincms3_api_cli" / "skills" / "feincms3-translate" / "SKILL.md"
        ).read_text()
        example = re.search(r"```json\n(.*?)```", text, re.DOTALL)[1]
        # The test app's types are testapp.*, not pages.*
        example = example.replace('"pages.', '"testapp.')

        def page(pk, slug, **kwargs):
            return Page.objects.create(
                pk=pk,
                title=slug,
                slug=slug,
                page_type="standard",
                is_active=True,
                **kwargs,
            )

        fr = page(5, "fr", language_code="fr", translation_of=self.home)
        angebot = page(17, "angebot", parent=self.home, language_code="de")
        page(18, "kurse", parent=angebot, language_code="de")
        kontakt = page(44, "kontakt", parent=self.home, language_code="de")
        page(45, "contact", parent=fr, language_code="fr", translation_of=kontakt)

        path = Path(self.dirs.name) / "translation.json"
        path.write_text(example)
        code, data, err = self.run_cli("batch", path)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(data["results"]), 2)
        self.assertFalse(Page.objects.filter(slug="offre").exists())


class ScriptsTest(SimpleTestCase):
    def test_scripts(self):
        scripts = tomllib.loads((CLI / "pyproject.toml").read_text())["project"][
            "scripts"
        ]
        self.assertEqual(set(scripts), {"f3api", "feincms3-api-cli"})
        for target in scripts.values():
            module, _sep, name = target.partition(":")
            self.assertTrue(callable(getattr(importlib.import_module(module), name)))

    def test_prog(self):
        for argv0, expected in [
            ("/home/x/.local/bin/f3api", "f3api"),
            ("/tmp/uv/bin/feincms3-api-cli", "feincms3-api-cli"),
            ("manage.py", "f3api"),
        ]:
            with mock.patch.object(sys, "argv", [argv0]):
                self.assertEqual(prog(), expected)


def command_lines(text):
    """
    The ``f3api …`` command lines of a skill, in code blocks and inline
    code, up to a pipe or a redirection
    """
    text = text.replace("\\\n", " ")
    candidates = [
        line.strip() for line in text.split("\n") if line.strip().startswith("f3api ")
    ]
    candidates += re.findall(r"`(f3api [^`]+)`", text)
    for candidate in candidates:
        args = []
        for token in shlex.split(candidate, comments=True)[1:]:
            if token[:1] in {"|", "<", ">", "&", ";"}:
                break
            args.append(token)
        yield candidate, args


def parses(args):
    """Parse without running: the code is 0, or 2 with click's message"""
    stderr = io.StringIO()
    with (
        mock.patch.object(click.Command, "invoke", lambda self, ctx: None),
        contextlib.redirect_stderr(stderr),
    ):
        return main(args), stderr.getvalue()


class SkillsTest(SimpleTestCase):
    def setUp(self):
        self.dirs = tempfile.TemporaryDirectory()
        self.addCleanup(self.dirs.cleanup)
        self.root = Path(self.dirs.name)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(list(args))
        return code, stdout.getvalue()

    def test_install_list_uninstall(self):
        shipped = self.root / "feincms3_api_cli" / "skills"
        for name in ["one", "two"]:
            (shipped / name).mkdir(parents=True)
            (shipped / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: >\n  Skill\n  {name}\n---\n\nText\n"
            )
        target = self.root / "skills"
        target.mkdir()
        # A directory of its own and a link to a skill f3api doesn't have
        (target / "two").mkdir()
        (target / "gone").symlink_to(shipped / "gone")

        with mock.patch.object(cli_skills, "SKILLS", shipped):
            code, out = self.run_cli("skills", "install")
            self.assertEqual(code, 0)
            self.assertIn("Linked one.", out)
            self.assertIn("left alone", out)
            self.assertIn("Removed gone.", out)
            self.assertEqual((target / "one").readlink(), shipped / "one")

            code, out = self.run_cli("skills", "list")
            self.assertEqual(
                [(s["name"], s["state"], s["description"]) for s in json.loads(out)],
                [
                    ("one", "linked", "Skill one"),
                    ("two", "a directory of its own", "Skill two"),
                ],
            )

            code, out = self.run_cli("skills", "uninstall")
            self.assertEqual(code, 0)
            self.assertFalse((target / "one").exists())
            self.assertTrue((target / "two").is_dir())

    def test_project(self):
        with mock.patch.object(Path, "cwd", return_value=self.root / "project"):
            code, out = self.run_cli("skills", "list", "--project")
        self.assertEqual(code, 0)
        self.assertTrue(
            all(skill["state"] == "not linked" for skill in json.loads(out))
        )

    def test_command_lines(self):
        self.assertEqual(
            list(command_lines("x `f3api get pages 1 --expand` y")),
            [("f3api get pages 1 --expand", ["get", "pages", "1", "--expand"])],
        )
        self.assertEqual(parses(["get", "pages", "1", "--expand"])[0], 0)
        self.assertEqual(parses(["get", "pages", "1", "--expanded"])[0], 2)

    def test_skills_parse(self):
        """A renamed option can't silently break a skill"""
        for skill in cli_skills._skills():
            text = (skill / "SKILL.md").read_text()
            self.assertTrue(cli_skills.description(skill), skill)
            for line, args in command_lines(text):
                with self.subTest(skill=skill.name, line=line):
                    code, err = parses(args)
                    self.assertEqual(code, 0, err)
