import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone

from feincms3_api.models import Upload
from testapp.models import Download
from testapp.test_write import WriteTestCase


class UploadTest(WriteTestCase):
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
        data = self.upload()
        response = self.put_download({"upload": data["upload"]}, dry_run="true")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Upload.objects.exists())
        self.download.refresh_from_db()
        self.assertTrue(self.download.file.name.startswith("downloads/preise"))

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
