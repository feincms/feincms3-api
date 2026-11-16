from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from feincms3_api.models import APIToken


class Command(BaseCommand):
    help = "Create an API token for a staff user and print its secret once."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", default="", help="What or who uses it")
        parser.add_argument(
            "--write", action="store_true", help="Allow changes, not only reading"
        )
        parser.add_argument("--days", type=int, help="Lifetime in days")

    def handle(self, *, username, name, write, days, **options):
        User = get_user_model()  # noqa: N806
        try:
            user = User._default_manager.get_by_natural_key(username)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user {username!r}.") from exc
        if not (user.is_active and user.is_staff):
            raise CommandError(f"{username!r} isn't an active staff user.")
        _token, secret = APIToken.objects.create_token(
            user=user,
            name=name or "management command",
            access=APIToken.Access.WRITE if write else APIToken.Access.READ,
            expires_at=timezone.now() + timedelta(days=days) if days else None,
        )
        self.stdout.write(secret)
