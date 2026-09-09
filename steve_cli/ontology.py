"""steve ontology — register, compile, and push VKG artifacts.

Commands:
  steve ontology register <file.yaml>               register a concept/binding YAML
  steve ontology compile  --root <uri> --binding <uri>   compile and print artifact info
  steve ontology push     --root <uri> --binding <uri>   compile + upload to Ontop via sidecar
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import requests
import yaml


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
    base_prefix: str | None,
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


@ontology.command("ls")
@click.argument("query", default="", required=False)
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--no-bindings", "show_bindings", is_flag=True, default=True, flag_value=False, help="Hide bindings section")
def ls_cmd(query: str, registry_url: str, show_bindings: bool):
    """List ontology packages (and bindings) in the registry."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    r = requests.get(f"{registry_url}/api/v1/search", params={"q": query, "limit": 100}, timeout=15)
    if r.status_code != 200:
        click.secho(f"Registry error: {r.status_code} {r.text[:200]}", fg="red", err=True)
        sys.exit(1)

    results = r.json().get("results", [])
    packages = [x for x in results if x.get("type") == "product"]

    click.secho(f"Packages ({len(packages)})", bold=True)
    if packages:
        width = max(len(p["uri"]) for p in packages)
        for p in packages:
            click.echo(f"  {p['uri']:<{width}}  {p.get('version', '')}")
    else:
        click.secho("  (none)", dim=True)

    if show_bindings:
        rb = requests.get(f"{registry_url}/api/v1/bindings", timeout=15)
        bindings = rb.json() if rb.status_code == 200 else []
        if query:
            bindings = [b for b in bindings if query.lower() in b.get("uri", "").lower()]
        click.echo()
        click.secho(f"Bindings ({len(bindings)})", bold=True)
        if bindings:
            width = max(len(b["uri"]) for b in bindings)
            for b in bindings:
                click.echo(f"  {b['uri']:<{width}}  {b.get('version', '')}")
        else:
            click.secho("  (none)", dim=True)


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
def compile_cmd(root: str, binding: str, base_prefix: str | None, registry_url: str, output_dir: Path | None):
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


def _workspace_push(
    manifest: dict,
    cwd: Path,
    registry_url: str,
    token: str,
) -> dict:
    all_paths = manifest.get("packages", []) + manifest.get("bindings", [])
    package_files = []
    for path in all_paths:
        full = cwd / path
        if not full.exists():
            click.secho(f"File not found: {full}", fg="red", err=True)
            sys.exit(1)
        package_files.append({"path": path, "content": full.read_text()})

    payload = {
        "manifest": manifest,
        "package_files": package_files,
    }
    r = requests.post(
        f"{registry_url}/api/v1/workspace/push",
        json=payload,
        headers=_headers(token),
        timeout=60,
    )
    if r.status_code != 200:
        click.secho(f"Workspace push failed: {r.status_code} {r.text[:400]}", fg="red", err=True)
        sys.exit(1)
    return r.json()


def _write_lock(artifact: dict, manifest: dict, lock_path: Path) -> None:
    import hashlib
    from datetime import datetime, timezone

    compile_cfg = manifest.get("compile", {})
    lock = {
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "root": compile_cfg.get("root"),
        "binding": compile_cfg.get("binding"),
        "manifest": artifact.get("manifest", {}),
        "artifacts": {
            "ontology_ttl_hash": "sha256:" + hashlib.sha256(artifact["ontology_ttl"].encode()).hexdigest(),
            "mappings_obda_hash": "sha256:" + hashlib.sha256(artifact["mappings_obda"].encode()).hexdigest(),
        },
    }
    lock_path.write_text(yaml.dump(lock, default_flow_style=False))


def _is_lock_current(manifest: dict, lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    lock = yaml.safe_load(lock_path.read_text()) or {}
    compile_cfg = manifest.get("compile", {})
    return (
        lock.get("root") == compile_cfg.get("root")
        and lock.get("binding") == compile_cfg.get("binding")
    )


def _push_to_ontop_sidecar(artifact: dict, sidecar_url: str) -> None:
    click.echo("Uploading VKG artifacts to Ontop sidecar — Ontop will restart (~10s) …")
    r = requests.post(
        f"{sidecar_url}/upload",
        json={
            "ontology_ttl": artifact["ontology_ttl"],
            "mappings_obda": artifact["mappings_obda"],
            "prefixes_properties": artifact["prefixes_properties"],
        },
        timeout=180,
    )
    if r.status_code != 200:
        click.secho(f"Sidecar upload failed: {r.status_code} {r.text[:400]}", fg="red", err=True)
        sys.exit(1)
    click.secho("✓ Ontop is ready with the new VKG artifacts", fg="green")


@ontology.command("push")
@click.option("--root", default=None, help="Root concept package URI (overrides ontology.yaml)")
@click.option("--binding", default=None, help="Binding package URI (overrides ontology.yaml)")
@click.option("--base-prefix", default=None, help="Override IRI namespace for generated properties")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--ontop-sidecar-url", envvar="ONTOP_SIDECAR_URL", default="http://localhost:18082", show_default=True)
@click.option("--frozen", is_flag=True, default=False, help="Skip if ontology.lock.yaml is current")
def push_cmd(
    root: str | None,
    binding: str | None,
    base_prefix: str | None,
    registry_url: str,
    ontop_sidecar_url: str,
    frozen: bool,
):
    """Compile VKG artifacts and push them to Ontop via the sidecar upload API."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    token = _get_token()
    cwd = Path.cwd()

    if root and binding:
        click.echo(f"Compiling {root} × {binding} …")
        artifact = _compile(root, binding, base_prefix, registry_url, token)
    else:
        manifest_path = cwd / "ontology.yaml"
        if not manifest_path.exists():
            click.secho("No --root/--binding provided and no ontology.yaml found in current directory.", fg="red", err=True)
            sys.exit(1)
        manifest = yaml.safe_load(manifest_path.read_text())
        if base_prefix:
            manifest.setdefault("compile", {})["base_prefix"] = base_prefix

        lock_path = cwd / "ontology.lock.yaml"
        if frozen and _is_lock_current(manifest, lock_path):
            click.secho("✓ Lock is current, skipping (--frozen)", fg="green")
            return

        click.echo("Pushing workspace from ontology.yaml …")
        artifact = _workspace_push(manifest, cwd, registry_url, token)
        _write_lock(artifact, manifest, lock_path)
        click.secho(f"✓ Lock written to {lock_path.name}", fg="cyan")

    m = artifact.get("manifest", {})
    click.secho(
        f"✓ Compiled: {m.get('concept_count', '?')} concepts, "
        f"{m.get('entity_count', '?')} entities, "
        f"{m.get('property_count', '?')} properties",
        fg="green",
    )

    _push_to_ontop_sidecar(artifact, ontop_sidecar_url)
