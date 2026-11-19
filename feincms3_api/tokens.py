"""
The authenticated user's tokens: listing them and revoking one, e.g. when
logging out, or a token which leaked or isn't needed anymore
"""

from django.utils import timezone

from feincms3_api.errors import APIError, ForbiddenError, NotFoundError
from feincms3_api.models import APIToken


def _wire(token, request):
    current = getattr(request, "api_token", None)
    return {
        "id": token.pk,
        "name": token.name,
        "prefix": token.prefix,
        "access": token.access,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "expires_at": token.expires_at,
        "current": current is not None and current.pk == token.pk,
    }


def list_tokens(request):
    """The user's tokens which haven't expired, newest first"""
    return [
        _wire(token, request)
        for token in APIToken.objects.filter(
            user=request.user, expires_at__gt=timezone.now()
        )
    ]


def revoke_token(request, pk):
    """
    Delete one of the user's tokens and return it

    Read-only tokens may only revoke themselves.
    """
    token = APIToken.objects.filter(
        user=request.user, expires_at__gt=timezone.now(), pk=pk
    ).first()
    if token is None:
        raise NotFoundError(f"No token {pk}.")
    current = getattr(request, "api_token", None)
    if (
        current is not None
        and current.access == APIToken.Access.READ
        and current.pk != token.pk
    ):
        raise ForbiddenError("A read-only token may only revoke itself.")
    wire = _wire(token, request)
    token.delete()
    return wire


def revoke_current_token(request):
    """Delete the token which authenticated the request and return it"""
    if (current := getattr(request, "api_token", None)) is None:
        raise APIError("This request wasn't authenticated with a token.")
    return revoke_token(request, current.pk)
