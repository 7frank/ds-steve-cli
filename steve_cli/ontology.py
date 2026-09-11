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
