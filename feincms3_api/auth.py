from django.utils.translation import gettext as _
from ninja.errors import HttpError
from ninja.security import HttpBearer, SessionAuthIsStaff

from feincms3_api.models import APIToken


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class TokenAuth(HttpBearer):
    """``Authorization: Bearer f3api_…`` with an ``APIToken``"""

    def authenticate(self, request, token):
        if (api_token := APIToken.objects.for_secret(token)) is None:
            return None
        if (
            api_token.access == APIToken.Access.READ
            and request.method not in SAFE_METHODS
        ):
            raise HttpError(403, _("This API token only allows reading."))
        request.user = api_token.user
        request.api_token = api_token
        return api_token.user


class StaffSessionAuth(SessionAuthIsStaff):
    """
    The session of a staff user, but not for requests with a bearer token

    An invalid token (expired, revoked) is a ``401`` then, not the CSRF
    failure of the session auth's check, which comes first.
    """

    def __call__(self, request):
        if request.headers.get("Authorization", "").lower().startswith("bearer "):
            return None
        return super().__call__(request)


def default_auth():
    """Tokens first: session auth fails without a CSRF token"""
    return [TokenAuth(), StaffSessionAuth()]
