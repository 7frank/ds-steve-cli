"""steve ontology — register, compile, and push VKG artifacts.

Commands:
  steve ontology register <file.yaml>               register a concept/binding YAML
  steve ontology compile  --root <uri> --binding <uri>   compile and print artifact info
  steve ontology push     --root <uri> --binding <uri>   compile + kubectl patch + restart
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import click
import requests
import yaml


def _get_registry_url() -> str:
    return os.getenv("REGISTRY_URL", "http://localhost:8765")


def _get_token() -> str:
    token = os.getenv("REGISTRY_TOKEN", "")
    if token:
        return token
    try:
        import jwt

        payload = {
            "sub": os.getenv("REGISTRY_USER", "steve-cli"),
            "email": os.getenv("REGISTRY_EMAIL", "steve@acme.com"),
            "org": os.getenv("REGISTRY_ORG", "acme"),
            "roles": ["admin"],
        }
        secret = os.getenv("REGISTRY_JWT_SECRET", "your-secret-key")
        return jwt.encode(payload, secret, algorithm="HS256")
    except ImportError:
        click.secho(
            "PyJWT not installed. Set REGISTRY_TOKEN or install pyjwt: uv add pyjwt",
            fg="red",
            err=True,
        )
        sys.exit(1)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _infer_type(data: dict) -> str:
    if "ontology_package" in data and ("entities" in data or "relations" in data):
        return "binding"
    return "concept"


def _compile(
    root_uri: str,
    binding_uri: str,
    base_prefix: Optional[str],
    registry_url: str,
    token: str,
) -> dict:
    payload: dict = {"root_uri": root_uri, "binding_uri": binding_uri}
    if base_prefix:
        payload["base_prefix"] = base_prefix
    r = requests.post(
        f"{registry_url}/api/v1/compile",
        json=payload,
        headers=_headers(token),
        timeout=60,
    )
    if r.status_code != 200:
        click.secho(f"Compile failed: {r.status_code} {r.text[:400]}", fg="red", err=True)
        sys.exit(1)
    return r.json()


@click.group("ontology")
def ontology():
    """Manage VKG ontology packages and compile artifacts."""
    pass


@ontology.command("register")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
def register_cmd(file: Path, registry_url: str):
    """Register a concept package or binding YAML with the registry."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    token = _get_token()
    data = yaml.safe_load(file.read_text())
    kind = _infer_type(data)

    if kind == "binding":
        endpoint = f"{registry_url}/api/v1/bindings"
        payload = {"binding": data}
    else:
        endpoint = f"{registry_url}/api/v1/register"
        payload = {"product": data}

    r = requests.post(endpoint, json=payload, headers=_headers(token), timeout=30)
    if r.status_code == 200:
        click.secho(f"✓ Registered {kind}: {data.get('uri')}", fg="green")
    else:
        click.secho(f"✗ Failed ({r.status_code}): {r.text[:300]}", fg="red", err=True)
        sys.exit(1)


@ontology.command("compile")
@click.option("--root", required=True, help="Root concept package URI, e.g. dp://o/acme/concepts/order/core")
@click.option("--binding", required=True, help="Binding package URI, e.g. dp://o/acme/bindings/order/iceberg")
@click.option("--base-prefix", default=None, help="Override IRI namespace for generated properties")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Write artifacts to this directory")
def compile_cmd(root: str, binding: str, base_prefix: Optional[str], registry_url: str, output_dir: Optional[Path]):
    """Compile VKG artifacts from registered packages."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    token = _get_token()
    click.echo(f"Compiling {root} × {binding} …")
    artifact = _compile(root, binding, base_prefix, registry_url, token)

    m = artifact.get("manifest", {})
    click.secho(
        f"✓ Compiled: {m.get('concept_count', '?')} concepts, "
        f"{m.get('entity_count', '?')} entities, "
        f"{m.get('property_count', '?')} properties, "
        f"{m.get('relation_count', '?')} relations",
        fg="green",
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "ontology.ttl").write_text(artifact["ontology_ttl"])
        (output_dir / "mappings.obda").write_text(artifact["mappings_obda"])
        (output_dir / "prefixes.properties").write_text(artifact["prefixes_properties"])
        (output_dir / "manifest.json").write_text(json.dumps(artifact["manifest"], indent=2))
        click.secho(f"  Artifacts written to {output_dir}", fg="cyan")


@ontology.command("push")
@click.option("--root", required=True, help="Root concept package URI")
@click.option("--binding", required=True, help="Binding package URI")
@click.option("--base-prefix", default=None, help="Override IRI namespace for generated properties")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--namespace", "-n", envvar="KUBE_NAMESPACE", default="jambit-data-stack-dev", show_default=True)
@click.option("--configmap", envvar="ONTOP_CONFIGMAP", default="ontop-vkg-artifacts", show_default=True)
@click.option("--no-restart", is_flag=True, default=False, help="Patch ConfigMap but skip rollout restart")
def push_cmd(
    root: str,
    binding: str,
    base_prefix: Optional[str],
    registry_url: str,
    namespace: str,
    configmap: str,
    no_restart: bool,
):
    """Compile VKG artifacts and push them to the Ontop ConfigMap, then restart Ontop."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    token = _get_token()
    click.echo(f"Compiling {root} × {binding} …")
    artifact = _compile(root, binding, base_prefix, registry_url, token)

    patch = {
        "data": {
            "ontology.ttl": artifact["ontology_ttl"],
            "mappings.obda": artifact["mappings_obda"],
            "prefixes.properties": artifact["prefixes_properties"],
        }
    }
    result = subprocess.run(
        ["kubectl", "patch", "configmap", configmap, "-n", namespace, "--type=merge", "-p", json.dumps(patch)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.secho(f"kubectl patch failed: {result.stderr}", fg="red", err=True)
        sys.exit(1)
    click.secho(f"✓ Patched ConfigMap {configmap}", fg="green")

    if no_restart:
        click.echo("Skipping rollout restart (--no-restart)")
        return

    result = subprocess.run(
        ["kubectl", "rollout", "restart", "deployment/ontop", "-n", namespace],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.secho(f"kubectl rollout restart failed: {result.stderr}", fg="red", err=True)
        sys.exit(1)

    click.echo("Waiting for Ontop rollout …")
    deadline = time.time() + 120
    while time.time() < deadline:
        res = subprocess.run(
            ["kubectl", "rollout", "status", "deployment/ontop", "-n", namespace, "--timeout=10s"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            click.secho("✓ Ontop rollout complete", fg="green")
            return
        time.sleep(5)
    click.secho("⚠ Timed out waiting for Ontop rollout", fg="yellow", err=True)
