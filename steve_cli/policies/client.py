from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

_POLICY_VERSION = "v0"


class PolicyClient:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
    ):
        self.endpoint = (endpoint or os.environ.get("PCP_ENDPOINT", "http://policy-control-plane:3010")).rstrip("/")
        self.token = token or os.environ.get("PCP_TOKEN", "")

    def _trpc(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.endpoint}/api/trpc/{path}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if method == "query":
            resp = httpx.get(url, params={"input": json.dumps(payload or {})}, headers=headers, timeout=10)
        else:
            resp = httpx.post(url, json=payload or {}, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"tRPC error on {path}: {data['error']}")
        return data.get("result", {}).get("data", data.get("result", {}))

    def register_pdp(self, name: str, endpoint: str, pdp_type: str = "opa", is_default: bool = True) -> dict:
        return self._trpc("mutation", "pdp.register", {
            "name": name,
            "type": pdp_type,
            "endpoint": endpoint,
            "isDefault": is_default,
        })

    def attach_pdp(self, workspace: str, pdp_id: str) -> None:
        self._trpc("mutation", "workspace.attachPdp", {"workspace": workspace, "pdpId": pdp_id})

    def apply_policies(self, workspace: str, pdp_name: str, rules: list[dict[str, Any]]) -> dict:
        return self._trpc("mutation", "workspace.applyPolicies", {
            "workspace": workspace,
            "fragments": [
                {
                    "syntax": "dsl",
                    "target": pdp_name,
                    "content": {"rules": rules},
                }
            ],
        })

    def apply_from_file(self, policies_file: Path | None = None) -> None:
        path = policies_file or Path.cwd() / "policies" / "access.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Policies file not found: {path}")

        import yaml
        config: dict[str, Any] = yaml.safe_load(path.read_text())
        workspace = config["workspace"]
        pdp_cfg = config["pdp"]
        rules = config.get("rules", [])

        pdp = self.register_pdp(
            name=pdp_cfg["name"],
            endpoint=pdp_cfg["endpoint"],
            pdp_type=pdp_cfg.get("type", "opa"),
        )
        self.attach_pdp(workspace, pdp["id"])
        result = self.apply_policies(workspace, pdp_cfg["name"], rules)

        errors = result.get("errors", [])
        if errors:
            reasons = "; ".join(e["reason"] for e in errors)
            raise RuntimeError(f"Failed to apply {len(errors)} policy fragment(s): {reasons}")
