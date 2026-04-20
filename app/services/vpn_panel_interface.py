"""
Abstract interface for VPN panel backend (Marzban/Pasarguard).
All panel services must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Optional


class VpnPanelInterface(ABC):
    """Common interface for VPN panel backends."""

    @abstractmethod
    async def get_system_stats(self) -> dict:
        """Return system stats (users online, traffic, etc.)."""
        ...

    @abstractmethod
    async def validate_connection(self) -> bool:
        """Check that the panel is reachable and credentials are valid."""
        ...

    @abstractmethod
    async def create_user(
        self,
        username: str,
        expire_days: int = 30,
        data_limit_gb: int = 0,
        **kwargs,
    ) -> dict:
        """
        Create a VPN user.
        Must return a dict that contains at least:
          - subscription_url: str  (full URL or path)
        """
        ...

    @abstractmethod
    async def get_user(self, username: str) -> Optional[dict]:
        """Return user info dict or None if not found."""
        ...

    @abstractmethod
    async def extend_user(self, username: str, extra_days: int) -> dict:
        """Extend user subscription by extra_days."""
        ...

    @abstractmethod
    async def disable_user(self, username: str) -> dict:
        """Disable / suspend a user."""
        ...

    @abstractmethod
    async def enable_user(self, username: str) -> dict:
        """Re-enable a previously disabled user."""
        ...

    @abstractmethod
    async def delete_user(self, username: str) -> None:
        """Permanently delete a user."""
        ...
