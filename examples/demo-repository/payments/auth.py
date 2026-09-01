"""Authentication for API endpoints."""

from __future__ import annotations

from payments.models import User
from payments.users import UserRepository


class AuthError(Exception):
    """Raised when authentication fails."""


def authenticate(users: UserRepository, api_key: str) -> User:
    """Authenticate a request by API key, returning the User or raising."""
    if not api_key:
        raise AuthError("missing api key")
    user = users.get_by_api_key(api_key)
    if user is None:
        raise AuthError("invalid api key")
    return user
