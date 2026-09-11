"""steve ontology — register, compile, and push VKG artifacts.

Commands:
  steve ontology register <file.yaml>               register a concept/binding YAML
  steve ontology compile  --root <uri> --binding <uri>   compile and print artifact info
  steve ontology push     --root <uri> --binding <uri>   compile + upload to VKG control plane
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
        secret = os.getenv("REGISTRY_JWT_SECRET", "your-secret-key-replace-with-32-plus-chars")
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


@ontology.command("init")
@click.option("-f", "--file", "manifest_file", default="ontology.yaml", show_default=True)
@click.option("--org", envvar="REGISTRY_ORG", default="acme", show_default=True)
@click.option("--name", default="my-domain", show_default=True)
@click.option("--force", is_flag=True, default=False)
def init_cmd(manifest_file: str, org: str, name: str, force: bool):
    """Create an ontology.yaml stub in the current directory."""
    if name == "my-domain":
        pyproject = Path.cwd() / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    tomllib = None
            if tomllib is not None:
                with open(pyproject, "rb") as f:
                    pdata = tomllib.load(f)
                inferred = pdata.get("project", {}).get("name", "")
                if inferred:
                    name = inferred

    path = Path.cwd() / manifest_file
    if path.exists() and not force:
        click.secho(f"✗ {manifest_file} already exists. Use --force to overwrite.", fg="red", err=True)
        sys.exit(1)

    stub = f"""\
# Ontology manifest — edit to match your domain
packages: []
#  - ontologies/{name.replace('-', '_')}_core.yaml

bindings: []
#  - ontologies/binding_{name.replace('-', '_')}_iceberg.yaml

compile:
  root: dp://o/{org}/concepts/{name}/core
  binding: dp://o/{org}/bindings/{name}/iceberg

dependencies: []
#  - dp://o/{org}/concepts/party/core
"""
    path.write_text(stub)
    click.secho(f"✓ Created {manifest_file}", fg="green")

    ontologies_dir = Path.cwd() / "ontologies"
    if not ontologies_dir.exists():
        ontologies_dir.mkdir()
        click.secho("✓ Created ontologies/", fg="green")

    _generate_schemas(Path.cwd())
    click.secho(f"✓ Generated schemas in vkg_modules/.schemas/", fg="green")

    safe_name = name.replace("-", "_")
    example_binding = ontologies_dir / f"binding_{safe_name}_iceberg.example.yaml"
    if not example_binding.exists():
        example_binding.write_text(f"""\
# yaml-language-server: $schema=../vkg_modules/.schemas/binding.schema.json
# Physical binding — maps ontology concepts to Trino/Iceberg tables.
# Rename to binding_{safe_name}_iceberg.yaml and add to ontology.yaml bindings: section.

# Unique binding identifier (dp:// URI scheme)
uri: "dp://o/{org}/bindings/{name}/iceberg"
version: "1.0.0"
description: "Physical binding of the {name} ontology to Trino/Iceberg tables"

# The concept package this binding implements
ontology_package: "dp://o/{org}/concepts/{name}/core"
# SemVer constraint — which versions of the ontology are compatible
ontology_version_constraint: "^1.0.0"

# Physical data source
source:
  adapter: trino-iceberg      # adapter type: trino-iceberg | trino | iceberg
  catalog: minio              # Trino catalog name
  schema: ws_{safe_name}_bronze  # Iceberg schema / warehouse name
  trino_host: trino           # hostname inside the cluster
  trino_port: 8080

# Map each ontology concept to a physical table.
# subject_template defines the RDF IRI — {{column}} references a row value.
entities:
  - concept: MyEntity                                       # concept name from the ontology
    subject_template: "https://{org}.com/{name}/entity/{{id}}"  # RDF subject IRI template
    table: minio.ws_{safe_name}_bronze.my_entities          # fully qualified Trino table
    id_column: id                                           # PK column referenced in subject_template

# Map ontology datatype properties to columns.
# Every property in the ontology concept that has a physical column gets an entry here.
properties:
  - concept: MyEntity
    property: myProperty   # property name from the ontology
    column: my_column      # physical column name

# Map ontology object properties (FK relationships) to RDF links.
# from_column (FK) joins to to_column (PK of the target entity).
relations: []
# Example:
#  - from_concept: Order
#    object_property: "https://{org}.com/ont/placedBy"
#    from_column: customer_id   # FK in the orders table
#    to_concept: Customer
#    to_column: customer_id     # PK in the customers table

metadata:
  owner: "your-team@{org}.com"
  tags:
    - binding
    - iceberg
    - {name}
""")
        click.secho(f"✓ Created {example_binding.name} (annotated example)", fg="green")


@ontology.command("schema")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Directory to write schemas into (default: vkg_modules/.schemas/)")
def schema_cmd(output_dir: Path | None):
    """Generate JSON Schemas for ontology package and binding YAML files."""
    cwd = Path.cwd()
    paths = _generate_schemas(cwd if output_dir is None else cwd)
    if output_dir:
        import shutil as _shutil
        output_dir.mkdir(parents=True, exist_ok=True)
        for key, src in paths.items():
            dst = output_dir / src.name
            _shutil.copy2(src, dst)
            paths[key] = dst
    click.secho(f"✓ package schema → {paths['package']}", fg="green")
    click.secho(f"✓ binding schema  → {paths['binding']}", fg="green")
    click.secho("Add this comment to your package YAML files:", dim=True)
    click.echo("  # yaml-language-server: $schema=../vkg_modules/.schemas/package.schema.json")
    click.secho("Add this comment to your binding YAML files:", dim=True)
    click.echo("  # yaml-language-server: $schema=../vkg_modules/.schemas/binding.schema.json")


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


@ontology.command("add")
@click.argument("uri")
@click.option("-f", "--file", "manifest_file", default="ontology.yaml", show_default=True, help="Manifest file to update")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
def add_cmd(uri: str, manifest_file: str, registry_url: str):
    """Add a remote dependency and download it into vkg_modules/ (like npm install <pkg>)."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    cwd = Path.cwd()
    path = cwd / manifest_file
    if path.exists():
        manifest = yaml.safe_load(path.read_text()) or {}
    else:
        manifest = {}

    deps = manifest.setdefault("dependencies", [])
    if uri not in deps:
        deps.append(uri)
        path.write_text(yaml.dump(manifest, default_flow_style=False, sort_keys=False))
        click.secho(f"✓ Added {uri} to {manifest_file}", fg="green")
    else:
        click.secho(f"  {uri} already in dependencies", dim=True)

    token = _get_token()
    written = _install_deps({"dependencies": [uri]}, cwd, registry_url, token)
    if written:
        click.secho(f"✓ Downloaded {written} package(s) into vkg_modules/", fg="green")
    else:
        click.secho(f"  Already up-to-date", dim=True)


def _collect_import_uris(data: dict) -> list[str]:
    """Extract all concept_imports URIs from a parsed package YAML dict."""
    return data.get("semantics", {}).get("concept_imports", [])


def _install_deps(manifest: dict, cwd: Path, registry_url: str, token: str, force: bool = False) -> int:
    """Recursively fetch all transitive imports into vkg_modules/<uri-path>/."""
    all_local_paths = manifest.get("packages", []) + manifest.get("bindings", [])
    queue: list[str] = list(manifest.get("dependencies", []))
    for path in all_local_paths:
        full = cwd / path
        if not full.exists():
            continue
        data = yaml.safe_load(full.read_text()) or {}
        queue.extend(_collect_import_uris(data))

    visited: set[str] = set()
    written = 0
    while queue:
        uri = queue.pop(0)
        if uri in visited:
            continue
        visited.add(uri)
        dest_dir = cwd / "vkg_modules" / _uri_to_path(uri)
        slug = uri.rstrip("/").split("/")[-1]
        dest_file = dest_dir / f"{slug}.yaml"
        if dest_file.exists() and not force:
            data = yaml.safe_load(dest_file.read_text()) or {}
            queue.extend(_collect_import_uris(data))
            continue
        path_part = uri.replace("dp://", "")
        r = requests.get(f"{registry_url}/api/v1/product/{path_part}", headers=_headers(token), timeout=15)
        if r.status_code == 404:
            click.secho(f"  Warning: {uri} not found in registry", fg="yellow")
            continue
        if r.status_code != 200:
            click.secho(f"  Warning: failed to fetch {uri}: {r.status_code}", fg="yellow")
            continue
        data = r.json()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
        click.secho(f"  ↓ {uri}", dim=True)
        written += 1
        queue.extend(_collect_import_uris(data))
    return written


@ontology.command("install")
@click.option("-f", "--file", "manifest_file", default="ontology.yaml", show_default=True, help="Manifest file to read")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--force", is_flag=True, default=False, help="Re-download even if already present")
def install_cmd(manifest_file: str, registry_url: str, force: bool):
    """Download all transitive import dependencies into vkg_modules/ (like npm install)."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    cwd = Path.cwd()
    manifest_path = cwd / manifest_file
    if not manifest_path.exists():
        click.secho(f"Manifest not found: {manifest_file}", fg="red", err=True)
        sys.exit(1)
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    token = _get_token()
    click.echo(f"Installing dependencies from {manifest_file} …")
    written = _install_deps(manifest, cwd, registry_url, token, force=force)
    click.secho(f"✓ {written} package(s) installed into vkg_modules/", fg="green")


@ontology.command("build")
@click.option("-f", "--file", "manifest_file", default="ontology.yaml", show_default=True, help="Manifest file to read")
@click.option("--base-prefix", default=None, help="Override IRI namespace for generated properties")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=Path("vkg_modules/.self"), show_default=True, help="Write compiled artifacts to this directory")
def build_cmd(manifest_file: str, base_prefix: str | None, output_dir: Path):
    """Resolve dependencies, compile VKG artifacts, and write them locally (no Ontop push)."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    cwd = Path.cwd()
    manifest_path = cwd / manifest_file
    if not manifest_path.exists():
        click.secho(f"Manifest not found: {manifest_file}", fg="red", err=True)
        sys.exit(1)

    manifest = yaml.safe_load(manifest_path.read_text())
    if base_prefix:
        manifest.setdefault("compile", {})["base_prefix"] = base_prefix

    trino_endpoint = os.getenv("TRINO_ENDPOINT")

    click.echo(f"Building from {manifest_file} …")
    artifact, tables = _compile_local(manifest, cwd)

    snapshots = _capture_snapshots(tables, trino_endpoint) if trino_endpoint and tables else {}
    if snapshots:
        click.secho(f"  Captured {len(snapshots)} Iceberg snapshot(s)", dim=True)

    lock_path = cwd / (Path(manifest_file).stem + ".lock.yaml")
    _write_lock(artifact, manifest, lock_path, snapshots or None)
    click.secho(f"✓ Lock written to {lock_path.name}", fg="cyan")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ontology.ttl").write_text(artifact["ontology_ttl"])
    (output_dir / "mappings.obda").write_text(artifact["mappings_obda"])
    (output_dir / "prefixes.properties").write_text(artifact["prefixes_properties"])
    (output_dir / "manifest.json").write_text(json.dumps(artifact["manifest"], indent=2))

    _write_local_packages(manifest, cwd)

    void_endpoint = os.getenv("VKG_CONTROL_PLANE_URL", os.getenv("ONTOP_SIDECAR_URL", "http://localhost:18081"))
    void_endpoint = void_endpoint.rstrip("/") + "/vkg/default/sparql"
    try:
        void_ttl = _generate_void_ttl(artifact["ontology_ttl"], void_endpoint)
        (output_dir / "void.ttl").write_text(void_ttl)
        click.secho(f"  VoID schema written to {output_dir}/void.ttl", fg="cyan")
    except Exception as e:
        click.secho(f"  Warning: VoID generation failed: {e}", fg="yellow")

    m = artifact.get("manifest", {})
    click.secho(
        f"✓ Built: {m.get('concept_count', '?')} concepts, "
        f"{m.get('entity_count', '?')} entities, "
        f"{m.get('property_count', '?')} properties",
        fg="green",
    )
    click.secho(f"  Artifacts written to {output_dir}/", fg="cyan")


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


def _resolve_closure_local(manifest: dict, cwd: Path) -> dict:
    """Resolve concept closure from local vkg_modules/ + local package files.

    Equivalent to registry service.resolve_closure() but reads from disk.
    Import order: deps first, then local packages (so child concepts override nothing,
    and the 'root' package's concepts come last — matching server behaviour).
    """
    compile_cfg = manifest.get("compile", {})
    root_uri = compile_cfg.get("root", "")

    visited: dict[str, str] = {}
    all_concepts: list[dict] = []
    errors: list[str] = []

    def _load_yaml_for_uri(uri: str) -> dict | None:
        dest_dir = cwd / "vkg_modules" / _uri_to_path(uri)
        slug = uri.rstrip("/").split("/")[-1]
        candidate = dest_dir / f"{slug}.yaml"
        if candidate.exists():
            return yaml.safe_load(candidate.read_text()) or {}
        for f in dest_dir.glob("*.yaml"):
            return yaml.safe_load(f.read_text()) or {}
        return None

    def _load_yaml_for_local_path(path: str) -> dict | None:
        full = cwd / path
        if full.exists():
            return yaml.safe_load(full.read_text()) or {}
        return None

    def _visit(uri: str, data: dict | None) -> None:
        base_uri = uri.split("@")[0] if "@" in uri else uri
        if base_uri in visited:
            return
        if data is None:
            errors.append(base_uri)
            return
        visited[base_uri] = data.get("version", "0.0.0")
        semantics = data.get("semantics") or {}
        for imp in semantics.get("concept_imports", []):
            dep_data = _load_yaml_for_uri(imp)
            _visit(imp, dep_data)
        for concept in semantics.get("concepts", []):
            typed_props = []
            for p in concept.get("typed_properties") or concept.get("properties") or []:
                if isinstance(p, dict):
                    typed_props.append(p)
            all_concepts.append({
                "name": concept["name"],
                "iri": concept.get("iri"),
                "description": concept.get("description"),
                "extends": concept.get("extends"),
                "typed_properties": typed_props,
                "properties": concept.get("properties"),
                "source_package": base_uri,
                "source_version": data.get("version", "0.0.0"),
            })

    for path in manifest.get("packages", []):
        data = _load_yaml_for_local_path(path)
        if data:
            uri = data.get("uri") or data.get("id", path)
            _visit(uri, data)

    return {"root": root_uri, "resolved": visited, "concepts": all_concepts, "errors": errors}


def _obda_iri_template(template: str, id_col: str) -> str:
    return template.replace(f"{{{id_col}}}", f"{{{id_col}}}")


def _xsd_iri(datatype: str) -> str:
    if datatype.startswith("http"):
        return datatype
    return datatype.replace("xsd:", "http://www.w3.org/2001/XMLSchema#")


def _find_iri(concepts: list[dict], name: str) -> str | None:
    for c in concepts:
        if c["name"] == name:
            return c.get("iri")
    return None


def _vkg_generate_ontology(closure: dict, base_prefix: str) -> str:
    lines: list[str] = []
    lines.append("@prefix owl:  <http://www.w3.org/2002/07/owl#> .")
    lines.append("@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .")
    lines.append("")
    seen_iris: set = set()
    for concept in closure.get("concepts", []):
        iri = concept.get("iri") or f"{base_prefix}{concept['name']}"
        if iri in seen_iris:
            continue
        seen_iris.add(iri)
        lines.append(f"<{iri}>")
        lines.append("    a owl:Class ;")
        desc = concept.get("description", "")
        if desc:
            escaped = desc.replace('"', '\\"')
            lines.append(f'    rdfs:label "{concept["name"]}" ;')
            lines.append(f'    rdfs:comment "{escaped}" ;')
        else:
            lines.append(f'    rdfs:label "{concept["name"]}" ;')
        extends = concept.get("extends")
        if extends:
            parent_iri = _find_iri(closure["concepts"], extends) or f"{base_prefix}{extends}"
            lines.append(f"    rdfs:subClassOf <{parent_iri}> ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")
        for prop in concept.get("typed_properties") or []:
            prop_name = prop["name"]
            obj_prop = prop.get("object_property")
            prop_iri = f"{base_prefix}{prop_name}"
            if obj_prop:
                lines.append(f"<{prop_iri}>")
                lines.append("    a owl:ObjectProperty ;")
                lines.append(f'    rdfs:label "{prop_name}" ;')
                lines.append(f"    rdfs:domain <{iri}> ;")
                lines.append(f"    rdfs:range <{obj_prop}> .")
                lines.append("")
            else:
                datatype = prop.get("datatype") or "xsd:string"
                xsd_full = _xsd_iri(datatype)
                lines.append(f"<{prop_iri}>")
                lines.append("    a owl:DatatypeProperty ;")
                lines.append(f'    rdfs:label "{prop_name}" ;')
                lines.append(f"    rdfs:domain <{iri}> ;")
                lines.append(f"    rdfs:range <{xsd_full}> .")
                lines.append("")
    return "\n".join(lines)


def _vkg_generate_mappings(closure: dict, binding_data: dict, base_prefix: str) -> str:
    lines: list[str] = []
    lines.append("[PrefixDeclaration]")
    seen_ns: set = set()
    for concept in closure.get("concepts", []):
        iri = concept.get("iri") or f"{base_prefix}{concept['name']}"
        ns = iri.rsplit("#", 1)[0] + "#" if "#" in iri else iri.rsplit("/", 1)[0] + "/"
        if ns not in seen_ns:
            seen_ns.add(ns)
            lines.append(f"{concept['name'].lower()}:\t{ns}")
    lines.append("")
    lines.append("[MappingDeclaration] @collection [[")
    lines.append("")

    entities = binding_data.get("entities", [])
    properties = binding_data.get("properties", [])
    relations = binding_data.get("relations", [])

    entity_map = {e["concept"]: e for e in entities}
    prop_map: dict[str, list] = {}
    for p in properties:
        prop_map.setdefault(p["concept"], []).append(p)

    for concept in closure.get("concepts", []):
        cname = concept["name"]
        entity = entity_map.get(cname)
        if entity is None:
            continue
        iri_template = entity["subject_template"]
        table = entity["table"]
        id_col = entity["id_column"]
        concept_iri = concept.get("iri") or f"{base_prefix}{cname}"
        mid = f"mapping-{cname.lower()}-class"
        lines.append(f"mappingId\t{mid}")
        lines.append(f"target\t\t<{_obda_iri_template(iri_template, id_col)}> a <{concept_iri}> .")
        lines.append(f"source\t\tSELECT {id_col} FROM {table}")
        lines.append("")
        for prop in concept.get("typed_properties") or []:
            pname = prop["name"]
            obj_prop = prop.get("object_property")
            col_binding = next((pb for pb in prop_map.get(cname, []) if pb["property"] == pname), None)
            if col_binding is None:
                continue
            col = col_binding["column"]
            prop_iri = f"{base_prefix}{pname}"
            datatype = prop.get("datatype") or "xsd:string"
            col_alias = f"_prop_{pname.lower()}" if col == id_col else col
            col_select = f"{col} AS {col_alias}" if col_alias != col else col
            mid2 = f"mapping-{cname.lower()}-{pname.lower()}"
            lines.append(f"mappingId\t{mid2}")
            if obj_prop:
                lines.append(
                    f"target\t\t<{_obda_iri_template(iri_template, id_col)}> <{prop_iri}>"
                    f" <{_obda_iri_template(obj_prop.replace('{', '{' + col_alias + '}'), col_alias)}> ."
                )
            else:
                lines.append(
                    f"target\t\t<{_obda_iri_template(iri_template, id_col)}> <{prop_iri}>"
                    f' "{{{col_alias}}}"^^<{_xsd_iri(datatype)}> .'
                )
            lines.append(f"source\t\tSELECT {id_col}, {col_select} FROM {table}")
            lines.append("")

    for rel in relations:
        from_entity = entity_map.get(rel["from_concept"])
        to_entity = entity_map.get(rel["to_concept"])
        if from_entity is None or to_entity is None:
            continue
        mid3 = f"mapping-rel-{rel['from_concept'].lower()}-{rel['to_concept'].lower()}"
        prop_iri = rel["object_property"] if rel["object_property"].startswith("http") else f"{base_prefix}{rel['object_property']}"
        fk_col = rel["from_column"]
        fk_alias = f"_fk_{rel['to_concept'].lower()}" if fk_col == from_entity["id_column"] else fk_col
        to_template = to_entity["subject_template"].replace(f"{{{rel['to_column']}}}", f"{{{fk_alias}}}")
        lines.append(f"mappingId\t{mid3}")
        lines.append(
            f"target\t\t<{_obda_iri_template(from_entity['subject_template'], from_entity['id_column'])}>"
            f" <{prop_iri}>"
            f" <{to_template}> ."
        )
        if fk_alias != fk_col:
            lines.append(f"source\t\tSELECT {from_entity['id_column']}, {fk_col} AS {fk_alias} FROM {from_entity['table']}")
        else:
            lines.append(f"source\t\tSELECT {from_entity['id_column']}, {fk_col} FROM {from_entity['table']}")
        lines.append("")

    lines.append("]]")
    return "\n".join(lines)


def _vkg_generate_prefixes(closure: dict, base_prefix: str) -> str:
    lines: list[str] = [
        "# Ontop prefix declarations",
        "owl=http://www.w3.org/2002/07/owl#",
        "rdf=http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs=http://www.w3.org/2000/01/rdf-schema#",
        "xsd=http://www.w3.org/2001/XMLSchema#",
    ]
    seen: set = set()
    for concept in closure.get("concepts", []):
        iri = concept.get("iri") or f"{base_prefix}{concept['name']}"
        ns = iri.rsplit("#", 1)[0] + "#" if "#" in iri else iri.rsplit("/", 1)[0] + "/"
        if ns not in seen:
            seen.add(ns)
            lines.append(f"{concept['name'].lower()}={ns}")
    return "\n".join(lines)


def _vkg_generate_manifest(closure: dict, binding_data: dict) -> dict:
    return {
        "vkg_version": "1.0",
        "root": closure["root"],
        "binding": binding_data.get("uri", ""),
        "binding_version": binding_data.get("version", ""),
        "ontology_package": binding_data.get("ontology_package", ""),
        "resolved": closure["resolved"],
        "concept_count": len(closure["concepts"]),
        "entity_count": len(binding_data.get("entities", [])),
        "property_count": len(binding_data.get("properties", [])),
        "relation_count": len(binding_data.get("relations", [])),
        "errors": closure.get("errors", []),
    }


def _compile_local(manifest: dict, cwd: Path) -> tuple[dict, list[str]]:
    """Compile VKG artifacts locally without calling the registry.

    Returns (artifact_dict, tables) matching the shape of _workspace_push().
    """
    compile_cfg = manifest.get("compile", {})
    base_prefix = compile_cfg.get("base_prefix") or "https://acme.com/ont/"

    closure = _resolve_closure_local(manifest, cwd)
    if closure["errors"]:
        click.secho(f"  Warning: unresolved imports: {closure['errors']}", fg="yellow")

    binding_path = None
    for path in manifest.get("bindings", []):
        binding_path = path
        break
    if not binding_path:
        click.secho("No bindings entry in manifest", fg="red", err=True)
        sys.exit(1)

    full = cwd / binding_path
    if not full.exists():
        click.secho(f"Binding file not found: {binding_path}", fg="red", err=True)
        sys.exit(1)
    binding_data = yaml.safe_load(full.read_text()) or {}

    ontology_ttl = _vkg_generate_ontology(closure, base_prefix)
    mappings_obda = _vkg_generate_mappings(closure, binding_data, base_prefix)
    prefixes_properties = _vkg_generate_prefixes(closure, base_prefix)
    manifest_data = _vkg_generate_manifest(closure, binding_data)
    tables = [e["table"] for e in binding_data.get("entities", [])]

    artifact = {
        "ontology_ttl": ontology_ttl,
        "mappings_obda": mappings_obda,
        "prefixes_properties": prefixes_properties,
        "manifest": manifest_data,
    }
    return artifact, tables


def _workspace_push(
    manifest: dict,
    cwd: Path,
    registry_url: str,
    token: str,
) -> tuple[dict, list[str]]:
    all_paths = manifest.get("packages", []) + manifest.get("bindings", [])
    package_files = []
    tables: list[str] = []
    for path in all_paths:
        full = cwd / path
        if not full.exists():
            click.secho(f"File not found: {full}", fg="red", err=True)
            sys.exit(1)
        content = full.read_text()
        package_files.append({"path": path, "content": content})
        data = yaml.safe_load(content)
        for entity in data.get("entities", []):
            if "table" in entity:
                tables.append(entity["table"])

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
    return r.json(), tables


def _capture_snapshots(tables: list[str], trino_endpoint: str) -> dict:
    import time
    snapshots: dict[str, int] = {}
    for fqt in tables:
        parts = fqt.split(".")
        if len(parts) != 3:
            continue
        catalog, schema, table = parts
        query = f'SELECT snapshot_id FROM {catalog}.{schema}."{table}$snapshots" ORDER BY committed_at DESC LIMIT 1'
        try:
            r = requests.post(
                f"{trino_endpoint}/v1/statement",
                data=query,
                headers={"X-Trino-User": "steve-cli"},
                timeout=15,
            )
            r.raise_for_status()
            body = r.json()
            rows: list = []
            for _ in range(30):
                rows.extend(body.get("data") or [])
                next_uri = body.get("nextUri")
                if not next_uri:
                    break
                time.sleep(0.2)
                body = requests.get(next_uri, timeout=15).json()
            rows.extend(body.get("data") or [])
            if rows:
                snapshots[fqt] = rows[0][0]
        except Exception:
            pass
    return snapshots


def _write_lock(artifact: dict, manifest: dict, lock_path: Path, snapshots: dict | None = None) -> None:
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
    if snapshots:
        lock["snapshots"] = snapshots
    lock_path.write_text(yaml.dump(lock, default_flow_style=False))


def _generate_void_ttl(ontology_ttl: str, endpoint_url: str) -> str:
    """Generate a VoID TTL string from a compiled OWL ontology TTL string.

    Reads OWL class/property declarations (owl:Class, rdfs:domain, rdfs:range,
    rdfs:subClassOf) and produces void:classPartition / void:propertyPartition
    triples that sparql-llm can use to understand the VKG schema.

    This approach is used instead of the sib-swiss void-generator Java CLI because
    Ontop is a Virtual Knowledge Graph: it only answers SPARQL patterns covered by
    its OBDA mappings. VoID metadata predicates (void:classPartition etc.) are not
    mapped to any SQL table, so void-generator discovery queries return 0 results.
    """
    import io
    import rdflib
    from rdflib.namespace import OWL, RDF, RDFS, XSD

    VOID = rdflib.Namespace("http://rdfs.org/ns/void#")
    VOID_EXT = rdflib.Namespace("http://ldf.fi/void-ext#")

    src = rdflib.Graph()
    src.parse(data=ontology_ttl, format="turtle")

    classes = {c for c in src.subjects(RDF.type, OWL.Class) if isinstance(c, rdflib.term.URIRef)}

    def _ancestors(cls: rdflib.term.URIRef) -> set:
        result: set = set()
        queue = list(src.objects(cls, RDFS.subClassOf))
        while queue:
            parent = queue.pop()
            if isinstance(parent, rdflib.term.URIRef) and parent not in result:
                result.add(parent)
                queue.extend(src.objects(parent, RDFS.subClassOf))
        return result

    out = rdflib.Graph()
    out.bind("void", VOID)
    out.bind("void-ext", VOID_EXT)
    out.bind("xsd", XSD)

    dataset = rdflib.URIRef(endpoint_url)
    out.add((dataset, RDF.type, VOID.Dataset))

    for cls in classes:
        cp = rdflib.BNode()
        out.add((dataset, VOID.classPartition, cp))
        out.add((cp, VOID["class"], cls))
        props: dict = {}
        for ancestor in ({cls} | _ancestors(cls)):
            for prop in src.subjects(RDFS.domain, ancestor):
                if isinstance(prop, rdflib.term.URIRef):
                    props[prop] = list(src.objects(prop, RDFS.range))
        for prop, ranges in props.items():
            pp = rdflib.BNode()
            out.add((cp, VOID.propertyPartition, pp))
            out.add((pp, VOID.property, prop))
            for rng in ranges:
                if not isinstance(rng, rdflib.term.URIRef):
                    continue
                if str(rng).startswith(str(XSD)):
                    dn = rdflib.BNode()
                    out.add((pp, VOID_EXT.datatypePartition, dn))
                    out.add((dn, VOID_EXT.datatype, rng))
                else:
                    cn = rdflib.BNode()
                    out.add((pp, VOID.classPartition, cn))
                    out.add((cn, VOID["class"], rng))

    buf = io.BytesIO()
    out.serialize(destination=buf, format="turtle")
    return buf.getvalue().decode()


def _is_lock_current(manifest: dict, lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    lock = yaml.safe_load(lock_path.read_text()) or {}
    compile_cfg = manifest.get("compile", {})
    return (
        lock.get("root") == compile_cfg.get("root")
        and lock.get("binding") == compile_cfg.get("binding")
    )


def _uri_to_path(uri: str) -> str:
    """Convert dp://o/acme/concepts/party/core → dp/o/acme/concepts/party/core."""
    return uri.replace("://", "/").replace("//", "/").rstrip("/")


def _trino_query(endpoint: str, sql: str) -> list[list]:
    """Execute SQL via Trino REST API and return all rows."""
    import time as _time
    r = requests.post(
        f"{endpoint}/v1/statement",
        data=sql,
        headers={"X-Trino-User": "steve-cli"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    rows: list = []
    for _ in range(60):
        rows.extend(body.get("data") or [])
        next_uri = body.get("nextUri")
        if not next_uri:
            break
        _time.sleep(0.15)
        body = requests.get(next_uri, timeout=15).json()
    rows.extend(body.get("data") or [])
    return rows


def _trino_describe_schema(endpoint: str, catalog: str, schema: str) -> dict[str, list[str]]:
    """Return {table_name: [col_name, ...]} for all tables in catalog.schema."""
    try:
        tables_rows = _trino_query(endpoint, f"SHOW TABLES IN {catalog}.{schema}")
    except Exception:
        return {}
    result: dict[str, list[str]] = {}
    for (tname,) in tables_rows:
        try:
            cols = _trino_query(endpoint, f"DESCRIBE {catalog}.{schema}.{tname}")
            result[tname] = [c[0] for c in cols]
        except Exception:
            result[tname] = []
    return result


def _load_binding(path: Path) -> tuple[str, dict]:
    """Return (header_comment, data_dict) from a binding YAML file."""
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines(keepends=True)
    header_lines: list[str] = []
    rest_lines: list[str] = []
    in_header = True
    for line in lines:
        if in_header and line.startswith("#"):
            header_lines.append(line)
        else:
            in_header = False
            rest_lines.append(line)
    header = "".join(header_lines)
    data = yaml.safe_load("".join(rest_lines)) or {}
    return header, data


def _save_binding(path: Path, header: str, data: dict) -> None:
    body = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    path.write_text(header + body)


def _get_concepts_from_manifest(manifest: dict, cwd: Path) -> list[dict]:
    """Return list of concept dicts (with name, typed_properties) from all local packages."""
    concepts: list[dict] = []
    seen_names: set[str] = set()

    def _add(c: dict) -> None:
        n = c.get("name", "")
        if n and n not in seen_names:
            seen_names.add(n)
            concepts.append(c)

    for pkg_path in manifest.get("packages", []):
        full = cwd / pkg_path
        if not full.exists():
            continue
        data = yaml.safe_load(full.read_text()) or {}
        for c in data.get("semantics", {}).get("concepts", []):
            _add(c)
    for uri in manifest.get("dependencies", []):
        dep_dir = cwd / "vkg_modules" / _uri_to_path(uri)
        slug = uri.rstrip("/").split("/")[-1]
        dep_file = dep_dir / f"{slug}.yaml"
        if dep_file.exists():
            data = yaml.safe_load(dep_file.read_text()) or {}
            for c in data.get("semantics", {}).get("concepts", []):
                _add(c)
    return concepts


def _resolve_binding_path(manifest: dict, cwd: Path, binding_opt: str | None) -> Path:
    if binding_opt:
        return cwd / binding_opt
    bindings = manifest.get("bindings", [])
    if bindings:
        return cwd / bindings[0]
    pkg_uri = manifest.get("compile", {}).get("root", "")
    name = pkg_uri.rstrip("/").split("/")[-1] if pkg_uri else "binding"
    return cwd / "ontologies" / f"binding_{name}_iceberg.yaml"


def _generate_schemas(cwd: Path) -> dict:
    schemas_dir = cwd / "vkg_modules" / ".schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    package_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "VKG Ontology Package",
        "description": "Concept package definition for the VKG (Virtual Knowledge Graph) pipeline",
        "type": "object",
        "required": ["uri", "version"],
        "additionalProperties": False,
        "properties": {
            "uri": {"type": "string", "description": "Unique package identifier, e.g. dp://o/acme/concepts/order/core", "pattern": "^dp://"},
            "version": {"type": "string", "description": "SemVer version string", "pattern": "^\\d+\\.\\d+\\.\\d+"},
            "description": {"type": "string", "description": "Human-readable description of this package"},
            "semantics": {
                "type": "object",
                "description": "Ontology definitions",
                "additionalProperties": False,
                "properties": {
                    "concept_imports": {
                        "type": "array",
                        "description": "URIs of packages whose concepts this package depends on",
                        "items": {"type": "string", "pattern": "^dp://"}
                    },
                    "concepts": {
                        "type": "array",
                        "description": "List of OWL class definitions",
                        "items": {
                            "type": "object",
                            "required": ["name"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "description": "Class name, e.g. Customer"},
                                "iri": {"type": "string", "description": "Full IRI for this class, e.g. https://acme.com/ont/order#Customer"},
                                "description": {"type": "string"},
                                "extends": {"type": "string", "description": "Name of parent concept (rdfs:subClassOf)"},
                                "typed_properties": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["name"],
                                        "additionalProperties": False,
                                        "properties": {
                                            "name": {"type": "string"},
                                            "datatype": {"type": "string", "description": "XSD datatype, e.g. xsd:string, xsd:integer, xsd:decimal"},
                                            "object_property": {"type": "string", "description": "Target class IRI if this is an object property (relation)"},
                                            "required": {"type": "boolean"},
                                            "description": {"type": "string"},
                                        },
                                    }
                                },
                                "properties": {"type": "array", "items": {"type": "object"}},
                            }
                        }
                    }
                }
            },
            "assets": {"type": "array", "items": {"type": "object"}},
            "metadata": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "owner": {"type": "string"},
                    "concept_only": {"type": "boolean"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                }
            }
        }
    }

    binding_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "VKG Physical Binding",
        "description": "Maps an ontology package's concepts to physical Trino/Iceberg tables",
        "type": "object",
        "required": ["uri", "version", "ontology_package", "source", "entities"],
        "additionalProperties": False,
        "properties": {
            "uri": {"type": "string", "description": "Unique binding identifier, e.g. dp://o/acme/bindings/order/iceberg", "pattern": "^dp://"},
            "version": {"type": "string", "description": "SemVer version string", "pattern": "^\\d+\\.\\d+\\.\\d+"},
            "description": {"type": "string"},
            "ontology_package": {"type": "string", "description": "URI of the concept package this binding implements", "pattern": "^dp://"},
            "ontology_version_constraint": {"type": "string", "description": "SemVer constraint on the ontology package version, e.g. ^1.0.0"},
            "source": {
                "type": "object",
                "required": ["adapter", "catalog", "schema"],
                "additionalProperties": False,
                "description": "Physical data source configuration",
                "properties": {
                    "adapter": {"type": "string", "description": "Storage adapter type", "enum": ["trino-iceberg", "trino", "iceberg"]},
                    "catalog": {"type": "string", "description": "Trino catalog name, e.g. minio"},
                    "schema": {"type": "string", "description": "Iceberg schema / warehouse name"},
                    "trino_host": {"type": "string", "description": "Trino hostname (used inside cluster)"},
                    "trino_port": {"type": "integer", "description": "Trino port, default 8080"},
                }
            },
            "entities": {
                "type": "array",
                "description": "Maps each ontology concept to a physical table",
                "items": {
                    "type": "object",
                    "required": ["concept", "subject_template", "table", "id_column"],
                    "additionalProperties": False,
                    "properties": {
                        "concept": {"type": "string", "description": "Concept name from the ontology package, e.g. Customer"},
                        "subject_template": {"type": "string", "description": "IRI template for the RDF subject, e.g. https://acme.com/order/customer/{customer_id}"},
                        "table": {"type": "string", "description": "Fully qualified Trino table name, e.g. minio.schema.customers"},
                        "id_column": {"type": "string", "description": "Primary key column used in the subject IRI template"},
                    }
                }
            },
            "properties": {
                "type": "array",
                "description": "Maps ontology datatype properties to table columns",
                "items": {
                    "type": "object",
                    "required": ["concept", "property", "column"],
                    "additionalProperties": False,
                    "properties": {
                        "concept": {"type": "string", "description": "Concept name, must match an entry in entities"},
                        "property": {"type": "string", "description": "Property name from the ontology concept"},
                        "column": {"type": "string", "description": "Physical column name in the table"},
                    }
                }
            },
            "relations": {
                "type": "array",
                "description": "Maps ontology object properties (FK joins) to RDF links",
                "items": {
                    "type": "object",
                    "required": ["from_concept", "object_property", "from_column", "to_concept", "to_column"],
                    "additionalProperties": False,
                    "properties": {
                        "from_concept": {"type": "string", "description": "Source concept name"},
                        "object_property": {"type": "string", "description": "Full IRI of the OWL object property"},
                        "from_column": {"type": "string", "description": "FK column in the source table"},
                        "to_concept": {"type": "string", "description": "Target concept name"},
                        "to_column": {"type": "string", "description": "PK column in the target table"},
                    }
                }
            },
            "metadata": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "owner": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                }
            }
        }
    }

    import json as _json
    pkg_path = schemas_dir / "package.schema.json"
    binding_path = schemas_dir / "binding.schema.json"
    pkg_path.write_text(_json.dumps(package_schema, indent=2))
    binding_path.write_text(_json.dumps(binding_schema, indent=2))
    return {"package": pkg_path, "binding": binding_path}


def _write_local_packages(manifest: dict, cwd: Path) -> None:
    """Write local workspace package files into vkg_modules/<uri-path>/."""
    all_paths = manifest.get("packages", []) + manifest.get("bindings", [])
    for path in all_paths:
        full = cwd / path
        if not full.exists():
            continue
        data = yaml.safe_load(full.read_text()) or {}
        uri = data.get("uri") or data.get("id")
        if not uri:
            continue
        dest = cwd / "vkg_modules" / _uri_to_path(uri)
        dest.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(full, dest / full.name)


def _push_to_ontop_sidecar(artifact: dict, sidecar_url: str, name: str = "default", cwd: Path | None = None) -> None:
    click.echo(f"Uploading VKG artifacts to VKG control plane (slot: {name}) …")
    sparql_url = sidecar_url.rstrip("/") + f"/vkg/{name}/sparql"
    void_ttl: str | None = None
    if cwd is not None:
        void_file = cwd / "vkg_modules" / ".self" / "void.ttl"
        if void_file.exists():
            void_ttl = void_file.read_text()
    if void_ttl is None:
        try:
            void_ttl = _generate_void_ttl(artifact["ontology_ttl"], sparql_url)
        except Exception as e:
            click.secho(f"  Warning: VoID generation failed: {e}", fg="yellow")
    examples_ttl: str | None = None
    if cwd is not None:
        examples_file = cwd / "vkg_modules" / ".self" / "examples.ttl"
        if examples_file.exists():
            examples_ttl = examples_file.read_text()
    payload = {
        "ontology_ttl": artifact["ontology_ttl"],
        "mappings_obda": artifact["mappings_obda"],
        "prefixes_properties": artifact["prefixes_properties"],
        "manifest": artifact.get("manifest", {}),
        "void_ttl": void_ttl,
        "examples_ttl": examples_ttl,
    }
    if name == "default":
        r = requests.post(f"{sidecar_url}/upload", json=payload, timeout=180)
    else:
        r = requests.post(f"{sidecar_url}/vkg/{name}/upload", json=payload, timeout=30)
        if r.status_code not in (200, 201):
            click.secho(f"Upload failed: {r.status_code} {r.text[:400]}", fg="red", err=True)
            sys.exit(1)
        r2 = requests.post(f"{sidecar_url}/vkg/{name}/start", timeout=180)
        if r2.status_code not in (200, 201):
            click.secho(f"Start failed: {r2.status_code} {r2.text[:400]}", fg="red", err=True)
            sys.exit(1)
        click.secho(f"✓ VKG slot '{name}' is ready", fg="green")
        return
    if r.status_code != 200:
        click.secho(f"Upload failed: {r.status_code} {r.text[:400]}", fg="red", err=True)
        sys.exit(1)
    click.secho("✓ Ontop is ready with the new VKG artifacts", fg="green")


@ontology.command("push")
@click.option("-f", "--file", "manifest_file", default=None, help="Manifest file (default: ontology.yaml)")
@click.option("--root", default=None, help="Root concept package URI (overrides manifest)")
@click.option("--binding", default=None, help="Binding package URI (overrides manifest)")
@click.option("--base-prefix", default=None, help="Override IRI namespace for generated properties")
@click.option("--registry-url", envvar="REGISTRY_URL", default="http://localhost:8765", show_default=True)
@click.option("--vkg-url", envvar="VKG_CONTROL_PLANE_URL", default=None, show_default=True)
@click.option("--name", "vkg_name", default="default", show_default=True, help="Named VKG slot on the control plane")
@click.option("--frozen", is_flag=True, default=False, help="Fail if lock file is absent or stale (like npm ci)")
def push_cmd(
    manifest_file: str | None,
    root: str | None,
    binding: str | None,
    base_prefix: str | None,
    registry_url: str,
    vkg_url: str | None,
    vkg_name: str,
    frozen: bool,
):
    """Compile VKG artifacts and push them to the VKG control plane."""
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)

    if vkg_url is None:
        vkg_url = os.getenv("VKG_CONTROL_PLANE_URL", os.getenv("ONTOP_SIDECAR_URL", "http://localhost:18081"))

    token = _get_token()
    cwd = Path.cwd()

    if root and binding:
        click.echo(f"Compiling {root} × {binding} …")
        artifact = _compile(root, binding, base_prefix, registry_url, token)
    else:
        chosen = manifest_file or "ontology.yaml"
        manifest_path = cwd / chosen
        if not manifest_path.exists():
            click.secho(f"Manifest not found: {chosen}", fg="red", err=True)
            sys.exit(1)
        manifest = yaml.safe_load(manifest_path.read_text())
        if base_prefix:
            manifest.setdefault("compile", {})["base_prefix"] = base_prefix

        lock_path = cwd / (Path(chosen).stem + ".lock.yaml")
        if frozen:
            if not lock_path.exists():
                click.secho(f"--frozen requires a lock file ({lock_path.name}) but none exists. Run without --frozen first.", fg="red", err=True)
                sys.exit(1)
            if not _is_lock_current(manifest, lock_path):
                click.secho(f"--frozen: lock file {lock_path.name} is stale. Run without --frozen to update.", fg="red", err=True)
                sys.exit(1)
            click.secho("✓ Lock is current (--frozen)", fg="green")
            return

        trino_endpoint = os.getenv("TRINO_ENDPOINT")
        click.echo(f"Pushing workspace from {chosen} …")
        artifact, tables = _compile_local(manifest, cwd)
        snapshots = _capture_snapshots(tables, trino_endpoint) if trino_endpoint and tables else {}
        if snapshots:
            click.secho(f"  Captured {len(snapshots)} Iceberg snapshot(s)", dim=True)
        _write_lock(artifact, manifest, lock_path, snapshots or None)
        click.secho(f"✓ Lock written to {lock_path.name}", fg="cyan")
        _write_local_packages(manifest, cwd)

    m = artifact.get("manifest", {})
    click.secho(
        f"✓ Compiled: {m.get('concept_count', '?')} concepts, "
        f"{m.get('entity_count', '?')} entities, "
        f"{m.get('property_count', '?')} properties",
        fg="green",
    )

    _push_to_ontop_sidecar(artifact, vkg_url, vkg_name, cwd=cwd)


# ---------------------------------------------------------------------------
# steve ontology bind  — interactive binding wizard
# ---------------------------------------------------------------------------

_BIND_OPTIONS = [
    click.option("-f", "--file", "manifest_file", default="ontology.yaml", show_default=True),
    click.option("-b", "--binding", "binding_file", default=None, help="Binding YAML path (default: first entry in manifest bindings:)"),
    click.option("--trino-endpoint", envvar="TRINO_ENDPOINT", default=None),
]


def _bind_options(f):
    for opt in reversed(_BIND_OPTIONS):
        f = opt(f)
    return f


def _bind_load(manifest_file: str, binding_file: str | None, trino_endpoint: str | None):
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)
    cwd = Path.cwd()
    manifest_path = cwd / manifest_file
    if not manifest_path.exists():
        click.secho(f"✗ {manifest_file} not found", fg="red", err=True)
        sys.exit(1)
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    binding_path = _resolve_binding_path(manifest, cwd, binding_file)
    concepts = _get_concepts_from_manifest(manifest, cwd)
    header, binding = _load_binding(binding_path)
    if not header:
        header = "# yaml-language-server: $schema=../vkg_modules/.schemas/binding.schema.json\n"
    source = binding.get("source", {})
    catalog = source.get("catalog", "minio")
    schema = source.get("schema", "")
    tables_cols: dict[str, list[str]] = {}
    if trino_endpoint and catalog and schema:
        click.secho(f"  Querying Trino {catalog}.{schema} …", dim=True)
        try:
            tables_cols = _trino_describe_schema(trino_endpoint, catalog, schema)
            click.secho(f"  Found {len(tables_cols)} table(s)", dim=True)
        except Exception as e:
            click.secho(f"  Warning: Trino unavailable ({e})", fg="yellow")
    elif trino_endpoint:
        click.secho("  Warning: binding has no source.catalog/schema — skipping Trino discovery", fg="yellow")
    return cwd, manifest, binding_path, header, binding, concepts, tables_cols


@click.group("bind")
def bind():
    """Interactive wizard to build/update a physical binding YAML."""


ontology.add_command(bind)


@bind.command("show")
@_bind_options
def bind_show_cmd(manifest_file: str, binding_file: str | None, trino_endpoint: str | None):
    """Show binding coverage: what's mapped and what's missing."""
    cwd, manifest, binding_path, header, binding, concepts, tables_cols = _bind_load(
        manifest_file, binding_file, trino_endpoint
    )

    click.secho(f"\nBinding: {binding_path.relative_to(cwd)}", bold=True)
    if not binding_path.exists():
        click.secho("  (file not yet created)", dim=True)

    entities_map = {e["concept"]: e for e in binding.get("entities", [])}
    props_map: dict[str, dict[str, str]] = {}
    for p in binding.get("properties", []):
        props_map.setdefault(p["concept"], {})[p["property"]] = p["column"]
    relations_list = binding.get("relations", [])

    bound_entities = 0
    click.secho("\nEntities:", bold=True)
    for c in concepts:
        name = c["name"]
        if name in entities_map:
            e = entities_map[name]
            click.secho(f"  ✓ {name:20s} → {e.get('table', '?')}  (id: {e.get('id_column', '?')})", fg="green")
            bound_entities += 1
        else:
            click.secho(f"  ✗ {name}", fg="red")
    click.secho(f"  {bound_entities}/{len(concepts)} bound")

    click.secho("\nProperties:", bold=True)
    bound_props = total_props = 0
    for c in concepts:
        cname = c["name"]
        for p in c.get("typed_properties", []):
            if p.get("object_property"):
                continue
            total_props += 1
            pname = p["name"]
            if pname in props_map.get(cname, {}):
                col = props_map[cname][pname]
                click.secho(f"  ✓ {cname}.{pname:25s} → {col}", fg="green")
                bound_props += 1
            else:
                click.secho(f"  ✗ {cname}.{pname}", fg="red")
    click.secho(f"  {bound_props}/{total_props} bound")

    click.secho("\nRelations:", bold=True)
    object_props = [(c["name"], p) for c in concepts for p in c.get("typed_properties", []) if p.get("object_property")]
    bound_rels = 0
    for from_concept, p in object_props:
        matched = [r for r in relations_list if r.get("from_concept") == from_concept and p["name"] in (r.get("object_property", "") + r.get("from_column", ""))]
        if matched:
            r = matched[0]
            click.secho(f"  ✓ {from_concept}.{p['name']:20s} → {r.get('from_column')} → {r.get('to_concept')}.{r.get('to_column')}", fg="green")
            bound_rels += 1
        else:
            click.secho(f"  ✗ {from_concept}.{p['name']}  (→ {p.get('object_property', '?')})", fg="red")
    click.secho(f"  {bound_rels}/{len(object_props)} bound")

    if tables_cols:
        all_mapped_cols: set[str] = set()
        for p in binding.get("properties", []):
            all_mapped_cols.add(p.get("column", ""))
        for e in binding.get("entities", []):
            all_mapped_cols.add(e.get("id_column", ""))
        for r in binding.get("relations", []):
            all_mapped_cols.add(r.get("from_column", ""))
            all_mapped_cols.add(r.get("to_column", ""))
        unmapped: list[str] = []
        for tname, cols in tables_cols.items():
            for col in cols:
                if col not in all_mapped_cols:
                    unmapped.append(f"{tname}.{col}")
        if unmapped:
            click.secho(f"\nUnmapped Trino columns ({len(unmapped)}):", bold=True)
            for u in unmapped:
                click.secho(f"  ~ {u}", dim=True)


@bind.command("entity")
@_bind_options
def bind_entity_cmd(manifest_file: str, binding_file: str | None, trino_endpoint: str | None):
    """Add or update an entity mapping (concept → table)."""
    import questionary
    cwd, manifest, binding_path, header, binding, concepts, tables_cols = _bind_load(
        manifest_file, binding_file, trino_endpoint
    )

    concept_names = [c["name"] for c in concepts]
    concept_name = questionary.select("Select concept:", choices=concept_names).ask()
    if not concept_name:
        return

    if tables_cols:
        table_choice = questionary.select(
            "Select table:",
            choices=list(tables_cols.keys()) + ["[enter manually]"],
        ).ask()
        if table_choice == "[enter manually]":
            table_choice = questionary.text("Table name (fully qualified):").ask()
        source = binding.get("source", {})
        fq_table = f"{source.get('catalog','minio')}.{source.get('schema','')}.{table_choice}" if "." not in (table_choice or "") else table_choice
    else:
        fq_table = questionary.text("Table (fully qualified, e.g. minio.schema.customers):").ask()
        table_choice = fq_table.split(".")[-1] if fq_table else ""

    cols = tables_cols.get(table_choice, [])
    if cols:
        id_col = questionary.select("ID column (for subject IRI):", choices=cols).ask()
    else:
        id_col = questionary.text("ID column:").ask()
    if not id_col:
        return

    org = manifest.get("compile", {}).get("root", "").split("/")[2] if "/" in manifest.get("compile", {}).get("root", "") else "acme"
    domain = manifest.get("compile", {}).get("root", "").rstrip("/").split("/")[-2] if "/" in manifest.get("compile", {}).get("root", "") else concept_name.lower()
    default_iri = f"https://{org}.com/{domain}/{concept_name.lower()}/{{{id_col}}}"
    subject_template = questionary.text("Subject IRI template:", default=default_iri).ask()
    if not subject_template:
        return

    entities = binding.setdefault("entities", [])
    existing = next((i for i, e in enumerate(entities) if e["concept"] == concept_name), None)
    entry = {"concept": concept_name, "subject_template": subject_template, "table": fq_table, "id_column": id_col}
    if existing is not None:
        entities[existing] = entry
        click.secho(f"✓ Updated entity {concept_name}", fg="green")
    else:
        entities.append(entry)
        click.secho(f"✓ Added entity {concept_name}", fg="green")

    _save_binding(binding_path, header, binding)
    click.secho(f"  Saved to {binding_path.relative_to(cwd)}", dim=True)


@bind.command("property")
@_bind_options
def bind_property_cmd(manifest_file: str, binding_file: str | None, trino_endpoint: str | None):
    """Add or update a datatype property mapping (concept.property → column)."""
    import questionary
    cwd, manifest, binding_path, header, binding, concepts, tables_cols = _bind_load(
        manifest_file, binding_file, trino_endpoint
    )

    concept_names = [c["name"] for c in concepts]
    concept_name = questionary.select("Select concept:", choices=concept_names).ask()
    if not concept_name:
        return
    concept = next(c for c in concepts if c["name"] == concept_name)

    data_props = [p for p in concept.get("typed_properties", []) if not p.get("object_property")]
    if not data_props:
        click.secho(f"  No datatype properties defined for {concept_name}", fg="yellow")
        return
    prop_choices = [f"{p['name']}  ({p.get('datatype','?')})" for p in data_props]
    chosen_label = questionary.select("Select property:", choices=prop_choices).ask()
    if not chosen_label:
        return
    prop_name = chosen_label.split()[0]

    entity = next((e for e in binding.get("entities", []) if e["concept"] == concept_name), None)
    tname = (entity.get("table", "") or "").split(".")[-1] if entity else ""
    cols = tables_cols.get(tname, []) if tname else []
    if cols:
        col = questionary.select(f"Map {prop_name} to column:", choices=cols + ["[enter manually]"]).ask()
        if col == "[enter manually]":
            col = questionary.text("Column name:").ask()
    else:
        col = questionary.text(f"Column name for {prop_name}:").ask()
    if not col:
        return

    properties = binding.setdefault("properties", [])
    existing = next((i for i, p in enumerate(properties) if p["concept"] == concept_name and p["property"] == prop_name), None)
    entry = {"concept": concept_name, "property": prop_name, "column": col}
    if existing is not None:
        properties[existing] = entry
        click.secho(f"✓ Updated {concept_name}.{prop_name} → {col}", fg="green")
    else:
        properties.append(entry)
        click.secho(f"✓ Added {concept_name}.{prop_name} → {col}", fg="green")

    _save_binding(binding_path, header, binding)
    click.secho(f"  Saved to {binding_path.relative_to(cwd)}", dim=True)


@bind.command("relation")
@_bind_options
def bind_relation_cmd(manifest_file: str, binding_file: str | None, trino_endpoint: str | None):
    """Add or update an object property / FK relation between two concepts."""
    import questionary
    cwd, manifest, binding_path, header, binding, concepts, tables_cols = _bind_load(
        manifest_file, binding_file, trino_endpoint
    )

    concepts_with_obj = [c for c in concepts if any(p.get("object_property") for p in c.get("typed_properties", []))]
    if not concepts_with_obj:
        click.secho("  No object properties (relations) defined in any concept", fg="yellow")
        return

    from_name = questionary.select("From concept:", choices=[c["name"] for c in concepts_with_obj]).ask()
    if not from_name:
        return
    from_concept = next(c for c in concepts if c["name"] == from_name)

    obj_props = [p for p in from_concept.get("typed_properties", []) if p.get("object_property")]
    obj_choices = [f"{p['name']}  (→ {p.get('object_property','?').split('#')[-1].split('/')[-1]})" for p in obj_props]
    chosen_label = questionary.select("Select object property:", choices=obj_choices).ask()
    if not chosen_label:
        return
    obj_prop = obj_props[obj_choices.index(chosen_label)]
    obj_prop_iri = obj_prop.get("object_property", "")

    concept_names = [c["name"] for c in concepts]
    to_name = questionary.select("To concept:", choices=concept_names).ask()
    if not to_name:
        return

    from_entity = next((e for e in binding.get("entities", []) if e["concept"] == from_name), None)
    from_tname = (from_entity.get("table", "") or "").split(".")[-1] if from_entity else ""
    from_cols = tables_cols.get(from_tname, []) if from_tname else []

    to_entity = next((e for e in binding.get("entities", []) if e["concept"] == to_name), None)
    to_tname = (to_entity.get("table", "") or "").split(".")[-1] if to_entity else ""
    to_cols = tables_cols.get(to_tname, []) if to_tname else []

    if from_cols:
        from_col = questionary.select("FK column (in from-table):", choices=from_cols + ["[enter manually]"]).ask()
        if from_col == "[enter manually]":
            from_col = questionary.text("FK column:").ask()
    else:
        from_col = questionary.text("FK column (in from-table):").ask()
    if not from_col:
        return

    if to_cols:
        to_col = questionary.select("PK column (in to-table):", choices=to_cols + ["[enter manually]"]).ask()
        if to_col == "[enter manually]":
            to_col = questionary.text("PK column:").ask()
    else:
        to_col = questionary.text("PK column (in to-table):").ask()
    if not to_col:
        return

    relations = binding.setdefault("relations", [])
    existing = next(
        (i for i, r in enumerate(relations) if r.get("from_concept") == from_name and r.get("from_column") == from_col and r.get("to_concept") == to_name),
        None,
    )
    entry = {
        "from_concept": from_name,
        "object_property": obj_prop_iri,
        "from_column": from_col,
        "to_concept": to_name,
        "to_column": to_col,
    }
    if existing is not None:
        relations[existing] = entry
        click.secho(f"✓ Updated relation {from_name}.{obj_prop['name']} → {to_name}", fg="green")
    else:
        relations.append(entry)
        click.secho(f"✓ Added relation {from_name}.{obj_prop['name']} → {to_name}", fg="green")

    _save_binding(binding_path, header, binding)
    click.secho(f"  Saved to {binding_path.relative_to(cwd)}", dim=True)
