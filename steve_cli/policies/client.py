from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

_POLICY_VERSION = "v0"


def _expand_schema(schema: str, sanitized_branch: str) -> str:
    if "__" in schema:
        return schema
    return f"{schema}__{sanitized_branch}"


def _expand_rules(rules: list[dict[str, Any]], sanitized_branch: str) -> list[dict[str, Any]]:
    expanded = []
    for rule in rules:
        rule = deepcopy(rule)
        for entry in rule.get("tables", []):
            if "schema" in entry:
                entry["schema"] = _expand_schema(entry["schema"], sanitized_branch)
        for entry in rule.get("columns", []):
            if "schema" in entry:
                entry["schema"] = _expand_schema(entry["schema"], sanitized_branch)
        expanded.append(rule)
    return expanded


class PolicyClient:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        workspace_id: str | None = None,
    ):
        from steve_cli.auth import get_service_url, get_token
        if endpoint is None:
            endpoint = get_service_url("pcp", workspace_id) or "http://policy-control-plane:3010"
        self.endpoint = endpoint.rstrip("/")
        self.token = token or get_token(workspace_id) or ""

    def __trpc(self, method: str, path: str, payload: dict | None = None) -> dict:
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
        result = data.get("result", {})
        return result.get("data", {}).get("json", result.get("data", result))

    def _trpc(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            return self.__trpc(method, path, payload)
        except httpx.ConnectError as e:
            raise EnvironmentError(
                f"Cannot reach Policy Control Plane at {self.endpoint} — "
                f"run `steve login` to configure credentials"
            ) from e

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

    def apply_from_file(self, policies_file: Path | None = None) -> tuple[str, str]:
        path = policies_file or Path.cwd() / "policies" / "access.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Policies file not found: {path}")

        import yaml
        config: dict[str, Any] = yaml.safe_load(path.read_text())
        workspace = config["workspace"]
        pdp_cfg = config["pdp"]
        rules = config.get("rules", [])

        from steve_cli.storage.branch import get_branch_prefix
        sanitized = get_branch_prefix()
        branch = sanitized
        rules = _expand_rules(rules, sanitized)

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

        return branch, sanitized
