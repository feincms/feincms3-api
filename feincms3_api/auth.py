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


def default_auth():
    """Tokens first: session auth fails without a CSRF token"""
    return [TokenAuth(), SessionAuthIsStaff()]
