from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class TrinoAuthStrategy(Protocol):
    def get_token(self) -> str | None: ...
    def get_service_url(self, service: str) -> str | None: ...


class CredentialsFileStrategy:
    """Default: reads token and service URLs from ~/.steve/credentials.json."""

    def __init__(self, workspace_id: str | None = None) -> None:
        self._workspace_id = workspace_id

    def get_token(self) -> str | None:
        from steve_cli.auth import get_token
        return get_token(self._workspace_id)

    def get_service_url(self, service: str) -> str | None:
        from steve_cli.auth import get_service_url
        return get_service_url(service, self._workspace_id)


class BearerTokenStrategy:
    """Explicit token + base URL for injected credentials (e.g. X-AK-Token header).

    Service URLs are constructed as https://<service>-<workspace_id>.<base_url>.
    """

    def __init__(self, token: str, workspace_id: str, base_url: str) -> None:
        self._token = token
        self._workspace_id = workspace_id
        self._base_url = base_url.rstrip("/")

    def get_token(self) -> str | None:
        return self._token or None

    def get_service_url(self, service: str) -> str | None:
        if not self._workspace_id or not self._base_url:
            return None
        return f"https://{service}-{self._workspace_id}.{self._base_url}"


class EnvVarStrategy:
    """Reads TRINO_TOKEN + TRINO_URL (or STEVE_BASE_URL + WORKSPACE_ID) from env.

    Useful in containers where credentials are injected as env vars rather than a file.
    """

    def get_token(self) -> str | None:
        return os.getenv("TRINO_TOKEN") or None

    def get_service_url(self, service: str) -> str | None:
        explicit = os.getenv(f"{service.upper()}_URL")
        if explicit:
            return explicit
        workspace_id = os.getenv("WORKSPACE_ID")
        base_url = os.getenv("STEVE_BASE_URL", "").rstrip("/")
        if workspace_id and base_url:
            return f"https://{service}-{workspace_id}.{base_url}"
        return None
