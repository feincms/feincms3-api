import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

from feincms3_api.conf import config


PREFIX = "f3api_"


KEY_SALT = "feincms3_api.APIToken"


def _hash(secret, key=None):
    """
    An HMAC-SHA256 of the secret with ``SECRET_KEY``, not a password hasher

    Password hashers are slow on purpose, against guessing secrets people
    choose; a token is 32 random bytes, which can't be guessed, and every
    request would pay for the slowness. Their salt would also need an id in
    the secret to find the row. The HMAC ties the tokens to ``SECRET_KEY``:
    a leaked database is useless without it, and rotating it without a
    fallback revokes every token at once. Tokens are found by this hash,
    through a unique index; there's no comparison of secrets in Python,
    which would need ``constant_time_compare``. A timing difference in the
    lookup could only tell an attacker about the HMACs of their own input,
    which they can't steer without the key.
    """
    return salted_hmac(KEY_SALT, secret, secret=key, algorithm="sha256").hexdigest()


def _hashes(secret):
    """The hash with the current key first, then with the fallbacks"""
    keys = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", ())]
    return [_hash(secret, key) for key in keys]


def default_expiry():
    return timezone.now() + config()["TOKEN_LIFETIME"]


class APITokenQuerySet(models.QuerySet):
    def create_token(self, *, user, name, access="read", expires_at=None):
        """Create a token, and return it with its secret, which isn't stored"""
        token = self.model(user=user, name=name, access=access)
        if expires_at is not None:
            token.expires_at = expires_at
        secret = token.set_secret()
        token.save()
        return token, secret

    def for_secret(self, secret):
        """The valid token with this secret, or ``None``"""
        if not secret or not secret.startswith(PREFIX):
            return None
        current, *fallbacks = _hashes(secret)
        token = (
            self.filter(hash__in=[current, *fallbacks], expires_at__gt=timezone.now())
            .select_related("user")
            .first()
        )
        if token is None or not (token.user.is_active and token.user.is_staff):
            return None
        if token.hash != current:
            # Found with a fallback key: move it to the current one, so it
            # survives the fallback being removed.
            type(token).objects.filter(pk=token.pk).update(hash=current)
            token.hash = current
        now = timezone.now()
        if token.last_used_at is None or token.last_used_at < now - timedelta(
            minutes=1
        ):
            type(token).objects.filter(pk=token.pk).update(last_used_at=now)
            token.last_used_at = now
        return token


class APIToken(models.Model):
    class Access(models.TextChoices):
        READ = "read", _("read only")
        WRITE = "write", _("read and write")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("user"),
        help_text=_("The token acts with this user's permissions, never more."),
    )
    name = models.CharField(
        _("name"), max_length=200, help_text=_("What or who uses the token.")
    )
    access = models.CharField(
        _("access"), max_length=10, choices=Access.choices, default=Access.READ
    )
    prefix = models.CharField(_("prefix"), max_length=20, editable=False)
    hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"), default=default_expiry)
    last_used_at = models.DateTimeField(
        _("last used at"), null=True, blank=True, editable=False
    )

    objects = APITokenQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("API token")
        verbose_name_plural = _("API tokens")

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    def set_secret(self):
        """Give the token a new secret and return it; only its hash is kept"""
        secret = PREFIX + secrets.token_urlsafe(32)
        self.prefix = secret[: len(PREFIX) + 6]
        self.hash = _hash(secret)
        return secret

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()
