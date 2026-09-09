"""steve vkg — runtime management of VKG instances on the control plane."""
from __future__ import annotations

import os
import sys

import click
import requests


def _vkg_url(ctx_url: str | None) -> str:
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    load_dotenv(".workspaces.env", override=False)
    if ctx_url is None:
        ctx_url = os.getenv("VKG_CONTROL_PLANE_URL", os.getenv("ONTOP_SIDECAR_URL", "http://localhost:18081"))
    return ctx_url.rstrip("/")


def _get(url: str, **kwargs) -> requests.Response:
    try:
        return requests.get(url, timeout=10, **kwargs)
    except requests.exceptions.ConnectionError:
        click.secho(f"Cannot reach VKG control plane at {url}", fg="red", err=True)
        sys.exit(1)


def _post(url: str, **kwargs) -> requests.Response:
    try:
        return requests.post(url, timeout=30, **kwargs)
    except requests.exceptions.ConnectionError:
        click.secho(f"Cannot reach VKG control plane at {url}", fg="red", err=True)
        sys.exit(1)


def _status_color(s: str) -> str:
    colors = {"running": "green", "starting": "yellow", "error": "red", "stopped": "white"}
    return click.style(s, fg=colors.get(s, "white"))


@click.group("vkg")
@click.option("--vkg-url", envvar="VKG_CONTROL_PLANE_URL", default=None)
@click.pass_context
def vkg(ctx: click.Context, vkg_url: str | None):
    """Manage VKG instances on the control plane."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = vkg_url


@vkg.command("ls")
@click.pass_context
def ls_cmd(ctx: click.Context):
    """List all VKG instances and their status."""
    base = _vkg_url(ctx.obj["url"])
    r = _get(f"{base}/vkg")
    r.raise_for_status()
    instances = r.json()
    if not instances:
        click.echo("No VKG instances found.")
        return
    click.echo(f"{'NAME':<20} {'STATUS':<12} {'CONCEPTS':>8} {'ENTITIES':>8} {'PROPS':>6}  SPARQL")
    click.echo("-" * 80)
    for i in instances:
        sparql = i.get("sparql_url") or "-"
        concepts = i.get("concept_count") or "-"
        entities = i.get("entity_count") or "-"
        props = i.get("property_count") or "-"
        click.echo(
            f"{i['name']:<20} {_status_color(i['status']):<21} {str(concepts):>8} {str(entities):>8} {str(props):>6}  {sparql}"
        )


@vkg.command("status")
@click.argument("name")
@click.pass_context
def status_cmd(ctx: click.Context, name: str):
    """Show detailed status of a VKG instance."""
    base = _vkg_url(ctx.obj["url"])
    r = _get(f"{base}/vkg/{name}/status")
    if r.status_code == 404:
        click.secho(f"VKG '{name}' not found.", fg="red", err=True)
        sys.exit(1)
    r.raise_for_status()
    d = r.json()
    click.echo(f"name:      {d['name']}")
    click.echo(f"status:    {_status_color(d['status'])}")
    click.echo(f"port:      {d.get('port') or '-'}")
    click.echo(f"sparql:    {d.get('sparql_url') or '-'}")
    if d.get("error"):
        click.secho(f"error:     {d['error']}", fg="red")


@vkg.command("start")
@click.argument("name")
@click.pass_context
def start_cmd(ctx: click.Context, name: str):
    """Start a stopped VKG instance."""
    base = _vkg_url(ctx.obj["url"])
    r = _post(f"{base}/vkg/{name}/start")
    if r.status_code == 404:
        click.secho(f"VKG '{name}' not found. Upload artifacts first.", fg="red", err=True)
        sys.exit(1)
    r.raise_for_status()
    d = r.json()
    click.secho(f"✓ {name}: {d['status']} (port {d['port']})", fg="green")


@vkg.command("stop")
@click.argument("name")
@click.pass_context
def stop_cmd(ctx: click.Context, name: str):
    """Stop a running VKG instance."""
    base = _vkg_url(ctx.obj["url"])
    r = _post(f"{base}/vkg/{name}/stop")
    if r.status_code == 404:
        click.secho(f"VKG '{name}' not found.", fg="red", err=True)
        sys.exit(1)
    r.raise_for_status()
    click.secho(f"✓ {name}: stopped", fg="yellow")


@vkg.command("sparql")
@click.argument("name")
@click.argument("query")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def sparql_cmd(ctx: click.Context, name: str, query: str, fmt: str):
    """Run a SPARQL query against a named VKG instance."""
    base = _vkg_url(ctx.obj["url"])
    r = _get(
        f"{base}/vkg/{name}/sparql",
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
    )
    if r.status_code == 503:
        click.secho(f"VKG '{name}' is not running.", fg="red", err=True)
        sys.exit(1)
    r.raise_for_status()
    data = r.json()
    if fmt == "json":
        import json
        click.echo(json.dumps(data, indent=2))
        return
    vars_ = data.get("head", {}).get("vars", [])
    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        click.echo("(no results)")
        return
    rows = [[b.get(v, {}).get("value", "") for v in vars_] for b in bindings]
    col_widths = [max(len(v), max((len(r[i]) for r in rows), default=0)) for i, v in enumerate(vars_)]
    header = "  ".join(v.ljust(col_widths[i]) for i, v in enumerate(vars_))
    sep = "  ".join("-" * w for w in col_widths)
    click.echo(header)
    click.echo(sep)
    for row in rows:
        click.echo("  ".join(row[i].ljust(col_widths[i]) for i in range(len(vars_))))
    click.echo(f"\n{len(bindings)} row(s)")


@vkg.command("logs")
@click.argument("name")
@click.option("--lines", "-n", default=50, show_default=True)
@click.pass_context
def logs_cmd(ctx: click.Context, name: str, lines: int):
    """Show Ontop process logs for a named VKG instance."""
    base = _vkg_url(ctx.obj["url"])
    r = _get(f"{base}/vkg/{name}/logs", params={"lines": lines})
    if r.status_code == 404:
        click.secho(r.json().get("detail", f"VKG '{name}' not found."), fg="red", err=True)
        sys.exit(1)
    r.raise_for_status()
    for line in r.json().get("lines", []):
        click.echo(line)
