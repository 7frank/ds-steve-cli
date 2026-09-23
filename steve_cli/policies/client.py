from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

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
        if isinstance(result, list):
            return result
        data_inner = result.get("data", result)
        if isinstance(data_inner, list):
            return data_inner
        if isinstance(data_inner, dict):
            return data_inner.get("json", data_inner)
        return data_inner

    def _trpc(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            return self.__trpc(method, path, payload)
        except httpx.ConnectError as e:
            raise EnvironmentError(
                f"Cannot reach Policy Control Plane at {self.endpoint} — "
                f"run `steve login` to configure credentials"
            ) from e

    def list_pdps(self) -> list[dict]:
        return self._trpc("query", "pdp.list") or []

    def list_workspaces(self) -> list[str]:
        return self._trpc("query", "workspace.list") or []

    def list_workspace_pdps(self, workspace: str) -> list[dict]:
        return self._trpc("query", "workspace.listPdps", {"workspace": workspace}) or []

    def delete_pdp(self, pdp_id: str) -> dict:
        return self._trpc("mutation", "pdp.delete", {"id": pdp_id})

    def delete_pdp_policy(self, policy_id: str) -> dict:
        return self._trpc("mutation", "pdp.policy.delete", {"id": policy_id})

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

    def _validate_against_trino(self, rules: list[dict[str, Any]], sanitized_branch: str) -> list[str]:
        workspace_id = os.environ.get("WORKSPACE_ID")
        workspace_name = os.environ.get("WORKSPACE_NAME", "")

        seen: dict[str, list[str]] = {}
        for rule in rules:
            for entry in list(rule.get("tables", [])) + list(rule.get("columns", [])):
                schema = entry.get("schema", "")
                table = entry.get("table", "")
                if schema and table:
                    seen.setdefault(schema, [])
                    if table not in seen[schema]:
                        seen[schema].append(table)

        results: list[str] = []
        for schema, tables in seen.items():
            tier = schema.split("__")[0].rsplit("_", 1)[-1] if "__" in schema else "bronze"
            ws_clean = workspace_name.lower().replace("-", "_") if workspace_name else ""
            catalog = f"{workspace_name}-{tier}" if workspace_name else f"minio-{tier}"

            try:
                from steve_cli.storage.trino import TrinoStorage
                storage = TrinoStorage(tier=tier, workspace=workspace_name or None)
                existing = storage._execute(f'SHOW TABLES FROM "{storage.catalog}".{schema}')
                existing_names = {r.get("Table", "") for r in existing}
                catalog = storage.catalog
            except Exception as exc:
                results.append(f"⚠ cannot reach Trino to validate schema '{schema}': {exc}")
                continue

            for table in tables:
                if table in existing_names:
                    results.append(f"✓ {catalog}.{schema}.{table}")
                else:
                    results.append(
                        f"⚠ table '{table}' not found in '{catalog}.{schema}'"
                        + (f" (found: {sorted(existing_names)})" if existing_names else " (schema empty or not found)")
                    )
        return results

    def apply_from_file(self, policies_file: Path | None = None, validate: Literal[None, "warn", "error"] = None) -> tuple[str, str]:
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

        if validate is not None:
            issues = self._validate_against_trino(rules, sanitized)
            failed = [msg for msg in issues if msg.startswith("⚠")]
            for msg in issues:
                print(msg)
            if failed and validate == "error":
                raise RuntimeError(f"Validation failed: {len(failed)} issue(s) found")

        return branch, sanitized
