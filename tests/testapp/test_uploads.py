import io
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from feincms3_api.models import Upload
from testapp.models import Download
from testapp.test_write import WriteTestCase


class UploadTest(WriteTestCase):
    def setUp(self):
        super().setUp()
        # A storage of its own, so that what's left in it is this test's
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def upload(self, name="liste.pdf", content=b"%PDF-1.4 neu"):
        response = self.client.post(
            "/api/uploads/", {"file": SimpleUploadedFile(name, content)}
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def put_download(self, file, **params):
        path = f"pages/{self.angebot.pk}/"
        return self.send(
            "patch",
            f"{path}plugins/testapp.download/{self.download.pk}/",
            {"fields": {"file": file}},
            etag=self.etag(path),
            **params,
        )

    def test_upload_and_use(self):
        data = self.upload()
        self.assertEqual(data["name"], "liste.pdf")
        self.assertEqual(data["size"], 12)
        upload = Upload.objects.get()
        staged = upload.file.name
        self.assertTrue(staged.startswith("feincms3_api/uploads/"))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.put_download({"upload": data["upload"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.download.refresh_from_db()
        # Saved by the plugin's field, with its own upload_to
        self.assertTrue(self.download.file.name.startswith("downloads/liste"))
        self.assertEqual(self.download.file.read(), b"%PDF-1.4 neu")
        self.assertEqual(
            response.json()["fields"]["file"]["name"], self.download.file.name
        )
        # Used once, then gone, file and all
        self.assertFalse(Upload.objects.exists())
        self.assertFalse(upload.file.storage.exists(staged))

    def test_create_plugin_with_upload(self):
        data = self.upload()
        path = f"pages/{self.home.pk}/"
        response = self.send(
            "put",
            f"{path}content/",
            {
                "content": {
                    "main": [
                        {
                            "type": "testapp.download",
                            "fields": {"file": {"upload": data["upload"]}},
                        }
                    ]
                }
            },
            etag=self.etag(path),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Download.objects.filter(parent=self.home).count(), 1)

    def test_dry_run_keeps_the_upload(self):
        data = self.upload(name="trocken.pdf")
        response = self.put_download({"upload": data["upload"]}, dry_run="true")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Upload.objects.exists())
        self.download.refresh_from_db()
        self.assertTrue(self.download.file.name.startswith("downloads/preise"))
        # The copy the plugin's field stored was removed again
        self.assertEqual(self.stored("trocken"), [])

    def stored(self, prefix):
        try:
            names = default_storage.listdir("downloads")[1]
        except FileNotFoundError:
            return []
        return [name for name in names if name.startswith(prefix)]

    def test_failed_batch_removes_stored_files(self):
        data = self.upload(name="batch.pdf")
        path = f"pages/{self.angebot.pk}/"
        operations = [
            {
                "op": "update_plugin",
                "resource": "pages",
                "id": self.angebot.pk,
                "etag": self.etag(path),
                "plugin": {"type": "testapp.download", "id": self.download.pk},
                "fields": {"file": {"upload": data["upload"]}},
            },
            {
                "op": "update",
                "resource": "pages",
                "id": self.angebot.pk,
                "fields": {"title": ""},
            },
        ]
        response = self.send("post", "batch/", {"operations": operations})
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(self.stored("batch"), [])
        self.assertTrue(Upload.objects.exists())

    def test_saved_write_keeps_the_stored_file(self):
        data = self.upload(name="bleibt.pdf")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.put_download({"upload": data["upload"]})
        self.assertEqual(response.status_code, 200, response.content)
        self.download.refresh_from_db()
        self.assertTrue(self.download.file.name.startswith("downloads/bleibt"))
        self.assertTrue(default_storage.exists(self.download.file.name))

    def test_keep_and_clear(self):
        current = self.get(f"pages/{self.angebot.pk}/").json()["content"]["main"][5]
        response = self.put_download(current["fields"]["file"])
        self.assertEqual(response.status_code, 200, response.content)
        self.download.refresh_from_db()
        self.assertTrue(self.download.file.name.startswith("downloads/preise"))

        # The field is required, so it can't be cleared
        response = self.put_download(None)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["path"], "fields.file")

    def test_unusable_uploads(self):
        data = self.upload()
        other = User.objects.create_superuser("other", "", "other")
        self.client.force_login(other)
        response = self.put_download({"upload": data["upload"]})
        self.assertEqual(response.status_code, 422)
        self.assertIn("No such upload", response.json()["errors"][0]["message"])

        self.client.force_login(self.superuser)
        for upload in ["nonsense", "00000000-0000-0000-0000-000000000000"]:
            with self.subTest(upload=upload):
                response = self.put_download({"upload": upload})
                self.assertEqual(response.status_code, 422)

        Upload.objects.update(created_at=timezone.now() - timedelta(days=2))
        response = self.put_download({"upload": data["upload"]})
        self.assertEqual(response.status_code, 422)

    def test_cleanup(self):
        self.upload()
        self.upload()
        upload = Upload.objects.first()
        Upload.objects.filter(pk=upload.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        out = io.StringIO()
        call_command("f3api_cleanup", stdout=out)
        self.assertIn("Removed 1 upload(s)", out.getvalue())
        self.assertEqual(Upload.objects.count(), 1)
        self.assertFalse(upload.file.storage.exists(upload.file.name))

    def test_anonymous(self):
        self.client.logout()
        response = self.client.post(
            "/api/uploads/", {"file": SimpleUploadedFile("a.txt", b"a")}
        )
        self.assertEqual(response.status_code, 401)
