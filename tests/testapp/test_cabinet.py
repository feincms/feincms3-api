import io

from cabinet.models import File, Folder
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from testapp.test_write import WriteTestCase


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "blue").save(buffer, "PNG")
    return buffer.getvalue()


class CabinetTest(WriteTestCase):
    def upload(self, name, content):
        response = self.client.post(
            "/api/uploads/", {"file": SimpleUploadedFile(name, content)}
        )
        return response.json()["upload"]

    def test_folders_and_files(self):
        response = self.send(
            "post", "resources/folders/", {"fields": {"name": "Bilder"}}
        )
        self.assertEqual(response.status_code, 201, response.content)
        folder = response.json()["id"]
        self.assertTrue(
            response.json()["meta"]["admin_url"].endswith(f"/folder/{folder}/")
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.send(
                "post",
                "resources/files/",
                {
                    "fields": {
                        "folder": {"type": "cabinet.folder", "id": folder},
                        "image_file": {"upload": self.upload("blau.png", png())},
                        "image_alt_text": "Ein blaues Rechteck",
                    }
                },
            )
        self.assertEqual(response.status_code, 201, response.content)
        file = File.objects.get()
        self.assertEqual((file.image_width, file.image_height), (40, 20))
        self.assertEqual(file.image_alt_text, "Ein blaues Rechteck")
        self.assertEqual(
            response.json()["fields"]["image_file"]["name"], file.image_file.name
        )

        data = self.get("resources/files/", folder=folder).json()
        self.assertEqual([item["id"] for item in data["items"]], [file.pk])

        # Not empty, so not deleted
        path = f"resources/folders/{folder}/"
        response = self.send("delete", path, etag=self.etag(path))
        self.assertEqual(response.status_code, 409)

    def test_exactly_one_file(self):
        response = self.send(
            "post",
            "resources/files/",
            {
                "fields": {
                    "image_file": {"upload": self.upload("blau.png", png())},
                    "download_file": {"upload": self.upload("liste.pdf", b"%PDF")},
                }
            },
        )
        # cabinet's own validation, through its admin form
        self.assertEqual(response.status_code, 422, response.content)
        paths = [error["path"] for error in response.json()["errors"]]
        self.assertIn("fields", paths)  # "Please fill in exactly one file field!"
        self.assertFalse(File.objects.exists())

    def test_folder_permissions(self):
        user = User.objects.create_user("staff", "", "staff", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_folder"))
        self.client.force_login(user)
        Folder.objects.create(name="Dokumente")
        self.assertEqual(self.get("resources/folders/").json()["count"], 1)
        response = self.send("post", "resources/folders/", {"fields": {"name": "x"}})
        self.assertEqual(response.status_code, 403)
