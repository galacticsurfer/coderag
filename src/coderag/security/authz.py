"""Repository authorization interface (ADR-006).

Retrieval is always scoped by ``repository_id``; this layer decides *which*
repositories a principal may reach at all. The MVP ships a permissive
development implementation — production deployments provide their own mapping of
principal -> allowed repositories.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthorizationProvider(ABC):
    @abstractmethod
    def allowed_repository_ids(self, principal: str | None) -> set[int] | None:
        """Return the allowed repository ids, or None to mean 'all repositories'."""

    def can_access(self, principal: str | None, repository_id: int) -> bool:
        allowed = self.allowed_repository_ids(principal)
        return allowed is None or repository_id in allowed


class AllowAllAuthorizationProvider(AuthorizationProvider):
    """Development default: every principal may access every repository."""

    def allowed_repository_ids(self, principal: str | None) -> set[int] | None:
        return None


_default = AllowAllAuthorizationProvider()


def get_authorization_provider() -> AuthorizationProvider:
    return _default
