from django.core.management.base import BaseCommand
from django.utils import timezone

from feincms3_api.models import APIToken, Upload


class Command(BaseCommand):
    help = "Remove expired uploads (and their files) and expired API tokens."

    def handle(self, **options):
        uploads = 0
        for upload in Upload.objects.expired():
            upload.discard()
            uploads += 1
        tokens, _details = APIToken.objects.filter(
            expires_at__lte=timezone.now()
        ).delete()
        self.stdout.write(f"Removed {uploads} upload(s) and {tokens} token(s).")
