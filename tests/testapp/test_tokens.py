import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone
from ninja.errors import HttpError

from feincms3_api.auth import TokenAuth
from feincms3_api.models import APIToken
from testapp.models import Page


class TokenTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", "", "staff", is_staff=True)
        self.page = Page.objects.create(
            title="Startseite", slug="de", language_code="de", page_type="standard"
        )

    def get(self, path, secret):
        return self.client.get(
            f"/api/{path}", headers={"authorization": f"Bearer {secret}"}
        )

    def test_bearer(self):
        superuser = User.objects.create_superuser("admin", "", "admin")
        token, secret = APIToken.objects.create_token(user=superuser, name="script")
        self.assertTrue(secret.startswith("f3api_"))
        self.assertTrue(secret.startswith(token.prefix))
        self.assertNotIn(secret, {token.hash, token.prefix})

        response = self.get(f"pages/{self.page.pk}/", secret)
        self.assertEqual(response.status_code, 200)
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)

        self.assertEqual(self.get("pages/", "f3api_wrong").status_code, 401)
        self.assertEqual(self.get("pages/", "wrong").status_code, 401)

    def test_permissions_are_the_users(self):
        _token, secret = APIToken.objects.create_token(user=self.staff, name="x")
        # A staff user without permissions sees nothing
        self.assertEqual(self.get("pages/", secret).status_code, 403)

    def test_invalid_tokens(self):
        token, secret = APIToken.objects.create_token(user=self.staff, name="x")

        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save()
        self.assertEqual(self.get("pages/", secret).status_code, 401)

        token.expires_at = timezone.now() + timedelta(days=1)
        token.save()
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.get("pages/", secret).status_code, 401)

        self.staff.is_staff = True
        self.staff.is_active = False
        self.staff.save()
        self.assertEqual(self.get("pages/", secret).status_code, 401)

    def test_read_only(self):
        _token, secret = APIToken.objects.create_token(user=self.staff, name="x")
        auth = TokenAuth()
        request = RequestFactory().get("/")
        self.assertEqual(auth.authenticate(request, secret), self.staff)
        self.assertEqual(request.user, self.staff)

        request = RequestFactory().post("/")
        with self.assertRaises(HttpError) as cm:
            auth.authenticate(request, secret)
        self.assertEqual(cm.exception.status_code, 403)

        _token, secret = APIToken.objects.create_token(
            user=self.staff, name="x", access=APIToken.Access.WRITE
        )
        self.assertEqual(
            auth.authenticate(RequestFactory().post("/"), secret), self.staff
        )

    def test_default_lifetime(self):
        token, _secret = APIToken.objects.create_token(user=self.staff, name="x")
        self.assertAlmostEqual(
            token.expires_at,
            timezone.now() + timedelta(hours=8),
            delta=timedelta(minutes=1),
        )

    def test_command(self):
        out = io.StringIO()
        call_command("f3api_token", "staff", "--write", "--hours=2", stdout=out)
        secret = out.getvalue().strip()
        token = APIToken.objects.for_secret(secret)
        self.assertEqual(token.user, self.staff)
        self.assertEqual(token.access, "write")

        with self.assertRaises(CommandError):
            call_command("f3api_token", "nobody")
        User.objects.create_user("user", "", "user")
        with self.assertRaises(CommandError):
            call_command("f3api_token", "user")


class TokenAdminTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", "", "staff", is_staff=True)
        self.other = User.objects.create_user("other", "", "other", is_staff=True)
        self.client.force_login(self.staff)

    def test_access_as_radio_buttons(self):
        response = self.client.get("/admin/feincms3_api/apitoken/add/")
        self.assertContains(response, 'type="radio" name="access" value="read"')

    def test_name_is_optional(self):
        response = self.client.post(
            "/admin/feincms3_api/apitoken/add/",
            {
                "name": "",
                "access": "read",
                "expires_at_0": "2099-01-01",
                "expires_at_1": "00:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        token = APIToken.objects.get()
        self.assertEqual(str(token), f"{token.prefix}…")

    def test_create_own_token(self):
        # No model permissions needed
        response = self.client.post(
            "/admin/feincms3_api/apitoken/add/",
            {
                "name": "Laptop",
                "access": "read",
                "expires_at_0": "2099-01-01",
                "expires_at_1": "00:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        token = APIToken.objects.get()
        self.assertEqual(token.user, self.staff)
        secret = response.context["secret"]
        self.assertContains(response, secret)
        self.assertEqual(APIToken.objects.for_secret(secret), token)

    def test_only_own_tokens(self):
        own, _secret = APIToken.objects.create_token(user=self.staff, name="own")
        other, _secret = APIToken.objects.create_token(user=self.other, name="other")
        response = self.client.get("/admin/feincms3_api/apitoken/")
        self.assertContains(response, "own")
        self.assertNotContains(response, "other (")
        self.assertEqual(
            self.client.get(
                f"/admin/feincms3_api/apitoken/{other.pk}/change/"
            ).status_code,
            302,  # The admin redirects for objects it can't find
        )
        self.client.post(
            f"/admin/feincms3_api/apitoken/{own.pk}/delete/", {"post": "yes"}
        )
        self.assertEqual(list(APIToken.objects.all()), [other])

    def test_superuser_creates_for_others(self):
        superuser = User.objects.create_superuser("admin", "", "admin")
        self.client.force_login(superuser)
        response = self.client.post(
            "/admin/feincms3_api/apitoken/add/",
            {
                "user": self.other.pk,
                "name": "Import",
                "access": "write",
                "expires_at_0": "2099-01-01",
                "expires_at_1": "00:00:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(APIToken.objects.get().user, self.other)


class TokenKeyTest(TestCase):
    def test_secret_key_rotation(self):
        staff = User.objects.create_user("staff", "", "staff", is_staff=True)
        with self.settings(SECRET_KEY="old-key"):
            token, secret = APIToken.objects.create_token(user=staff, name="x")
        old_hash = token.hash

        # Rotated with a fallback: still valid, and moved to the new key
        with self.settings(SECRET_KEY="new-key", SECRET_KEY_FALLBACKS=["old-key"]):
            self.assertEqual(APIToken.objects.for_secret(secret), token)
        token.refresh_from_db()
        self.assertNotEqual(token.hash, old_hash)
        with self.settings(SECRET_KEY="new-key"):
            self.assertEqual(APIToken.objects.for_secret(secret), token)

        # Rotated without a fallback: revoked
        with self.settings(SECRET_KEY="newer-key"):
            self.assertIsNone(APIToken.objects.for_secret(secret))
