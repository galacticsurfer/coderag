"""User persistence."""

from __future__ import annotations

from payments.models import User


class UserRepository:
    def __init__(self) -> None:
        self._users: dict[int, User] = {}
        self._by_key: dict[str, User] = {}

    def save(self, user: User) -> User:
        """Persist a user and index it by API key for authentication."""
        self._users[user.id] = user
        if user.api_key:
            self._by_key[user.api_key] = user
        return user

    def get(self, user_id: int) -> User | None:
        return self._users.get(user_id)

    def get_by_api_key(self, api_key: str) -> User | None:
        return self._by_key.get(api_key)
