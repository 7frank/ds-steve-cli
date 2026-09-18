from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_CREDENTIALS_FILE = Path.home() / ".steve" / "credentials.json"


def load_credentials() -> dict:
    if _CREDENTIALS_FILE.exists():
        try:
            return json.loads(_CREDENTIALS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_credentials(
    workspace_id: str,
    token: str,
    base_url: Optional[str] = None,
    workspace_name: Optional[str] = None,
    expires_at: Optional[str] = None,
    s3_endpoint: Optional[str] = None,
) -> None:
    _CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    creds = load_credentials()
    workspaces = creds.get("workspaces", {})
    entry: dict = {"token": token}
    if base_url:
        entry["base_url"] = base_url.rstrip("/")
    if workspace_name:
        entry["workspace_name"] = workspace_name
    if expires_at:
        entry["expires_at"] = expires_at
    if s3_endpoint:
        entry["s3_endpoint"] = s3_endpoint
    workspaces[workspace_id] = entry
    creds["workspaces"] = workspaces
    _CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2))
    _CREDENTIALS_FILE.chmod(0o600)


def clear_credentials(workspace_id: Optional[str] = None) -> None:
    if workspace_id is None:
        if _CREDENTIALS_FILE.exists():
            _CREDENTIALS_FILE.unlink()
        return
    creds = load_credentials()
    workspaces = creds.get("workspaces", {})
    workspaces.pop(workspace_id, None)
    creds["workspaces"] = workspaces
    _CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2))
    _CREDENTIALS_FILE.chmod(0o600)


def _get_workspace_entry(workspace_id: Optional[str]) -> dict:
    creds = load_credentials()
    workspaces = creds.get("workspaces", {})
    if workspace_id and workspace_id in workspaces:
        return workspaces[workspace_id]
    if workspaces:
        return next(iter(workspaces.values()), {})
    return {}


def get_token(workspace_id: Optional[str] = None) -> Optional[str]:
    return _get_workspace_entry(workspace_id).get("token")


def get_s3_endpoint(workspace_id: Optional[str] = None) -> Optional[str]:
    return _get_workspace_entry(workspace_id).get("s3_endpoint")


def get_service_url(service: str, workspace_id: Optional[str] = None) -> Optional[str]:
    entry = _get_workspace_entry(workspace_id)
    base_url = entry.get("base_url")
    if not base_url or not workspace_id:
        return None
    return f"https://{service}-{workspace_id}.{base_url}"
