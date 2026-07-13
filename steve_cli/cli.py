#!/usr/bin/env python3
"""
Steve CLI - A simple tool to run jobs from jobs.yaml with proper environment setup.

Usage:
    steve jobs ls              List all available jobs
    steve jobs run <job-name>  Run a job with its environment variables
    steve setup all            Decrypt SOPS-encrypted .env files
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

import click
from dotenv import load_dotenv, dotenv_values
import questionary
import yaml


class JobsConfig:
    def __init__(self, jobs_file: Optional[Path] = None):
        self.jobs_file = jobs_file or Path.cwd() / "jobs.yaml"
        self.jobs: List[Dict[str, Any]] = []
        self._load_jobs()

    def _load_jobs(self) -> None:
        try:
            if not self.jobs_file.exists():
                click.echo(f"❌ Error: jobs.yaml not found at {self.jobs_file}", err=True)
                click.echo("Make sure you're in a directory with a jobs.yaml file", err=True)
                sys.exit(1)

            with open(self.jobs_file, 'r') as f:
                data = yaml.safe_load(f)

            if not data or 'jobs' not in data:
                click.echo("❌ Error: Invalid jobs.yaml format. Expected 'jobs' key at root", err=True)
                sys.exit(1)

            self.jobs = data['jobs']

        except yaml.YAMLError as e:
            click.echo(f"❌ Error parsing jobs.yaml: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Error reading jobs.yaml: {e}", err=True)
            sys.exit(1)

    def get_job(self, job_name: str) -> Optional[Dict[str, Any]]:
        for job in self.jobs:
            if job.get('name') == job_name:
                return job
        return None

    def list_jobs(self) -> List[str]:
        return [job.get('name', 'unnamed') for job in self.jobs]


def run_job_command(job: Dict[str, Any]) -> int:
    command = job.get('command', [])
    if not command:
        click.echo(f"❌ Error: No command specified for job '{job.get('name')}'", err=True)
        return 1

    env = os.environ.copy()
    for env_filename in [".env", ".workspaces.env"]:
        env_path = Path.cwd() / env_filename
        if env_path.exists():
            env.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
    job_env = job.get('env', {})
    for key, value in job_env.items():
        env[key] = str(value)

    job_name = job.get('name', 'unnamed')
    click.echo(f"🚀 Running job: {click.style(job_name, fg='blue', bold=True)}")
    click.echo(f"📝 Command: {click.style(' '.join(command), fg='cyan')}")

    if job_env:
        click.echo("🌍 Environment variables:")
        for key, value in job_env.items():
            click.echo(f"   {click.style(key, fg='green')}={click.style(str(value), fg='yellow')}")

    click.echo()

    try:
        result = subprocess.run(command, env=env, cwd=Path.cwd())
        return result.returncode
    except FileNotFoundError:
        click.echo(f"❌ Error: Command not found: {command[0]}", err=True)
        return 127
    except Exception as e:
        click.echo(f"❌ Error running command: {e}", err=True)
        return 1


@click.group()
def main():
    """Steve CLI - Run jobs and manage environment setup."""
    pass


@main.group(invoke_without_command=True)
@click.option('--jobs-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to jobs.yaml file (default: ./jobs.yaml)')
@click.pass_context
def jobs(ctx: click.Context, jobs_file: Optional[Path]):
    """Manage and run jobs from jobs.yaml."""
    if ctx.invoked_subcommand is not None:
        return
    config = JobsConfig(jobs_file)
    job_names = config.list_jobs()
    if not job_names:
        click.echo("❌ No jobs found in jobs.yaml")
        return
    choice = questionary.select(
        "Select a job to run:",
        choices=job_names,
    ).ask()
    if choice is None:
        sys.exit(0)
    job = config.get_job(choice)
    sys.exit(run_job_command(job))


@jobs.command('ls')
@click.option('--jobs-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to jobs.yaml file (default: ./jobs.yaml)')
def jobs_list(jobs_file: Optional[Path]):
    """List all available jobs."""
    config = JobsConfig(jobs_file)
    job_names = config.list_jobs()

    if not job_names:
        click.echo("❌ No jobs found in jobs.yaml")
        return

    click.echo(f"📋 Available jobs in {click.style(str(config.jobs_file), fg='cyan')}:")
    click.echo()

    for job_data in config.jobs:
        name = job_data.get('name', 'unnamed')
        command = job_data.get('command', [])
        cron = job_data.get('cron')
        depends_on = job_data.get('dependsOn', [])
        env_vars = job_data.get('env', {})

        click.echo(f"  {click.style(name, fg='blue', bold=True)}")
        if command:
            click.echo(f"    Command: {click.style(' '.join(command), fg='cyan')}")
        if cron:
            click.echo(f"    Schedule: {click.style(cron, fg='yellow')}")
        if depends_on:
            click.echo(f"    Depends on: {click.style(', '.join(depends_on), fg='magenta')}")
        if env_vars:
            click.echo(f"    Environment: {click.style(f'{len(env_vars)} variables', fg='green')}")
            for key, value in env_vars.items():
                click.echo(f"      {key}={value}")
        click.echo()


@jobs.command('run')
@click.argument('job_name')
@click.option('--jobs-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to jobs.yaml file (default: ./jobs.yaml)')
def jobs_run(job_name: str, jobs_file: Optional[Path]):
    """Run a job by name."""
    config = JobsConfig(jobs_file)
    job = config.get_job(job_name)
    if not job:
        available = config.list_jobs()
        click.echo(f"❌ Error: Job '{job_name}' not found", err=True)
        if available:
            click.echo(f"Available jobs: {', '.join(available)}", err=True)
        sys.exit(1)
    sys.exit(run_job_command(job))


APPS_DIR = Path.home() / ".steve" / "apps"
AUTH_PROXY_INTERNAL_URL = "http://auth-proxy-service"
APP_NAME_PREFIX = "term-"


class AppsConfig:
    def __init__(self, apps_file: Optional[Path] = None):
        self.apps_file = apps_file or Path.cwd() / "apps.yaml"
        self.apps: List[Dict[str, Any]] = []
        self._load_apps()

    def _load_apps(self) -> None:
        try:
            if not self.apps_file.exists():
                click.echo(f"❌ Error: apps.yaml not found at {self.apps_file}", err=True)
                click.echo("Make sure you're in a directory with an apps.yaml file", err=True)
                sys.exit(1)

            with open(self.apps_file, 'r') as f:
                data = yaml.safe_load(f)

            if not data or 'apps' not in data:
                click.echo("❌ Error: Invalid apps.yaml format. Expected 'apps' key at root", err=True)
                sys.exit(1)

            self.apps = data['apps']

        except yaml.YAMLError as e:
            click.echo(f"❌ Error parsing apps.yaml: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Error reading apps.yaml: {e}", err=True)
            sys.exit(1)

    def get_app(self, app_name: str) -> Optional[Dict[str, Any]]:
        for app in self.apps:
            if app.get('name') == app_name:
                return app
        return None

    def list_apps(self) -> List[str]:
        return [app.get('name', 'unnamed') for app in self.apps]


def _get_session_name() -> Optional[str]:
    return os.environ.get('SESSION_NAME') or socket.gethostname()


def _pid_file(app_name: str) -> Path:
    return APPS_DIR / f"{app_name}.pid"


def _url_file(app_name: str) -> Path:
    return APPS_DIR / f"{app_name}.url"


def _get_running_pid(app_name: str) -> Optional[int]:
    pf = _pid_file(app_name)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        pf.unlink(missing_ok=True)
        return None


def _register_app(session_name: str, app_name: str, port: int) -> Optional[Dict[str, Any]]:
    import urllib.request
    import json
    url = f"{AUTH_PROXY_INTERNAL_URL}/api/internal/register-app"
    payload = json.dumps({"sessionName": session_name, "appName": app_name, "port": port}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        click.echo(f"⚠️  Failed to register app with auth-proxy: {e}", err=True)
        return None


def _deregister_app(session_name: str, app_name: str) -> None:
    import urllib.request
    import json
    url = f"{AUTH_PROXY_INTERNAL_URL}/api/internal/register-app"
    payload = json.dumps({"sessionName": session_name, "appName": app_name}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


@main.group(invoke_without_command=True)
@click.option('--apps-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to apps.yaml file (default: ./apps.yaml)')
@click.pass_context
def apps(ctx: click.Context, apps_file: Optional[Path]):
    """Manage and run apps from apps.yaml inside the terminal."""
    if ctx.invoked_subcommand is not None:
        return
    config = AppsConfig(apps_file)
    app_names = config.list_apps()
    if not app_names:
        click.echo("❌ No apps found in apps.yaml")
        return
    choice = questionary.select(
        "Select an app to start:",
        choices=app_names,
    ).ask()
    if choice is None:
        sys.exit(0)
    app_def = config.get_app(choice)
    ctx.invoke(apps_start, app_name=choice, apps_file=apps_file)


@apps.command('ls')
@click.option('--apps-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to apps.yaml file (default: ./apps.yaml)')
def apps_list(apps_file: Optional[Path]):
    """List all available apps."""
    config = AppsConfig(apps_file)
    if not config.apps:
        click.echo("❌ No apps found in apps.yaml")
        return

    click.echo(f"📋 Available apps in {click.style(str(config.apps_file), fg='cyan')}:")
    click.echo()

    for app_data in config.apps:
        name = app_data.get('name', 'unnamed')
        command = app_data.get('command', [])
        port = app_data.get('port', '?')
        env_vars = app_data.get('env', {})
        pid = _get_running_pid(name)
        status = click.style('● running', fg='green') if pid else click.style('○ stopped', fg='yellow')

        click.echo(f"  {click.style(name, fg='blue', bold=True)}  {status}")
        if command:
            click.echo(f"    Command: {click.style(' '.join(command), fg='cyan')}")
        click.echo(f"    Port: {click.style(str(port), fg='magenta')}")
        if pid:
            uf = _url_file(name)
            url = uf.read_text().strip() if uf.exists() else f'http://localhost:{port}'
            click.echo(f"    URL: {click.style(url, fg='cyan')}")
        if env_vars:
            click.echo(f"    Environment: {click.style(f'{len(env_vars)} variables', fg='green')}")
        click.echo()


@apps.command('start')
@click.argument('app_name')
@click.option('--apps-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to apps.yaml file (default: ./apps.yaml)')
def apps_start(app_name: str, apps_file: Optional[Path]):
    """Start an app by name and register it with the auth-proxy."""
    config = AppsConfig(apps_file)
    app_def = config.get_app(app_name)
    if not app_def:
        available = config.list_apps()
        click.echo(f"❌ Error: App '{app_name}' not found", err=True)
        if available:
            click.echo(f"Available apps: {', '.join(available)}", err=True)
        sys.exit(1)

    session_name = _get_session_name()
    if not session_name:
        click.echo("❌ Error: SESSION_NAME environment variable not set. Are you running inside the terminal?", err=True)
        sys.exit(1)

    existing_pid = _get_running_pid(app_name)
    if existing_pid:
        click.echo(f"⚠️  App '{app_name}' is already running (PID {existing_pid})")
        uf = _url_file(app_name)
        if uf.exists():
            click.echo(f"🌐 Access at: {click.style(uf.read_text().strip(), fg='cyan')}")
        return

    command = app_def.get('command', [])
    port = app_def.get('port')
    env_vars = app_def.get('env', {})

    if not command:
        click.echo(f"❌ Error: No command specified for app '{app_name}'", err=True)
        sys.exit(1)
    if not port:
        click.echo(f"❌ Error: No port specified for app '{app_name}'", err=True)
        sys.exit(1)

    env = os.environ.copy()
    for env_filename in [".env", ".workspaces.env"]:
        env_path = config.apps_file.parent / env_filename
        if env_path.exists():
            env.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
    for key, value in env_vars.items():
        env[key] = str(value)

    APPS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = APPS_DIR / f"{app_name}.log"

    click.echo(f"🚀 Starting app: {click.style(app_name, fg='blue', bold=True)}")
    click.echo(f"📝 Command: {click.style(' '.join(command), fg='cyan')}")
    click.echo(f"🔌 Port: {click.style(str(port), fg='magenta')}")

    app_cwd = config.apps_file.parent
    with open(log_file, 'a') as lf:
        proc = subprocess.Popen(command, env=env, cwd=app_cwd, stdout=lf, stderr=lf)

    time.sleep(1)
    if proc.poll() is not None:
        click.echo(f"❌ App '{app_name}' exited immediately (code {proc.returncode})", err=True)
        click.echo(f"📄 Last log output:", err=True)
        try:
            with open(log_file) as lf:
                click.echo(lf.read(), err=True)
        except Exception:
            pass
        sys.exit(1)

    _pid_file(app_name).write_text(str(proc.pid))

    result = _register_app(session_name, f"{APP_NAME_PREFIX}{app_name}", port)

    if result:
        click.echo(f"✅ App started (PID {proc.pid}) and registered with auth-proxy")
        public_url = result.get('url')
    else:
        click.echo(f"✅ App started (PID {proc.pid}), but registration with auth-proxy failed")
        public_url = None

    if public_url:
        click.echo(f"🌐 Access at: {click.style(public_url, fg='cyan')}")
    else:
        public_url = f'http://localhost:{port}'
        click.echo(f"🌐 Access at (local): {click.style(public_url, fg='yellow')}")

    _url_file(app_name).write_text(public_url)

    click.echo(f"📄 Logs: {click.style(str(log_file), fg='yellow')}")


@apps.command('stop')
@click.argument('app_name')
@click.option('--apps-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to apps.yaml file (default: ./apps.yaml)')
def apps_stop(app_name: str, apps_file: Optional[Path]):
    """Stop a running app and deregister it from the auth-proxy."""
    session_name = _get_session_name()

    pid = _get_running_pid(app_name)
    if not pid:
        click.echo(f"⚠️  App '{app_name}' is not running")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        click.echo(f"🛑 Stopped app '{app_name}' (PID {pid})")
    except OSError as e:
        click.echo(f"❌ Failed to stop process: {e}", err=True)

    _pid_file(app_name).unlink(missing_ok=True)
    _url_file(app_name).unlink(missing_ok=True)

    if session_name:
        _deregister_app(session_name, f"{APP_NAME_PREFIX}{app_name}")
        click.echo(f"🔌 Deregistered from auth-proxy")


SETUP_IGNORE = [".env.example", ".env.template"]


@main.group()
def setup():
    """Setup commands for environment configuration."""
    pass


@setup.command("env")
def setup_env():
    """Decrypt SOPS-encrypted .env files in current directory and write plaintext .env files."""
    cwd = Path.cwd()
    found: List[Path] = []
    for pattern in ["*.enc.env", "*.encrypted.env"]:
        found.extend(sorted(cwd.glob(pattern)))

    found = [f for f in found if f.name not in SETUP_IGNORE]

    if not found:
        click.echo("No *.enc.env or *.encrypted.env files found.")
        return

    for enc_file in found:
        click.echo(f"\n📄 Found: {click.style(enc_file.name, fg='blue', bold=True)}")

        content = enc_file.read_text()
        if "ENC[" not in content and "sops:" not in content:
            click.echo("   ⚠️  Not SOPS encrypted, skipping.")
            continue

        result = subprocess.run(
            ["sops", "-d", str(enc_file)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            click.echo(f"   ❌ Decryption failed: {result.stderr.strip()}", err=True)
            continue

        keys = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.append(line.split("=", 1)[0])

        if keys:
            click.echo(f"   🔑 Keys: {click.style(', '.join(keys), fg='green')}")

        stem = enc_file.name.replace(".encrypted.env", "").replace(".enc.env", "")
        out_file = cwd / f"{stem}.env"
        out_file.write_text(result.stdout)

        click.echo(f"   ✅ Decrypted -> {click.style(out_file.name, fg='cyan')}")
        cmd = f"""set -a
        source {out_file.name}
        set +a"""

        click.echo(f"   💡 Run:\n{click.style(cmd, fg='yellow')}")

def _detect_workspaces() -> List[str]:
    tiers = {"BRONZE", "SILVER", "GOLD"}
    workspaces = set()
    for key in os.environ:
        if key.endswith("_ACCESS_KEY"):
            prefix = key[: -len("_ACCESS_KEY")]
            if prefix not in tiers:
                workspaces.add(prefix)
    return sorted(workspaces)


def _build_tree(keys: List[str]) -> Dict[str, Any]:
    tree: Dict[str, Any] = {}
    for key in keys:
        parts = key.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    return tree


def _print_tree(node: Dict[str, Any], prefix: str = "", is_last: bool = True) -> None:
    items = sorted(node.items())
    for i, (name, child) in enumerate(items):
        last = i == len(items) - 1
        connector = "└── " if last else "├── "
        click.echo(f"{prefix}{connector}{name}")
        if child is not None:
            extension = "    " if last else "│   "
            _print_tree(child, prefix + extension, last)


def _list_bucket(storage_kwargs: dict, label: str, bucket_name: str) -> None:
    click.echo(f"  {click.style(label, fg='cyan')} ({bucket_name})")
    try:
        from steve_cli.storage import S3Storage
        storage = S3Storage(**storage_kwargs)
        keys = storage.list_all()
        if not keys:
            click.echo("    (empty)")
        else:
            tree = _build_tree(keys)
            _print_tree(tree, prefix="    ")
    except EnvironmentError as e:
        click.echo(f"    ⚠️  {e}", err=True)
    except Exception as e:
        click.echo(f"    ❌ {e}", err=True)


@main.command("buckets")
@click.option('--env-file', '-e', type=click.Path(path_type=Path), multiple=True,
              help='Path to .env file(s). Can be specified multiple times. Defaults to .env and .workspace.env')
def buckets(env_file: tuple):
    """List all S3 buckets detected from env variables and show their files as a tree."""
    cwd = Path.cwd()
    env_files = [Path(f) for f in env_file] if env_file else [cwd / ".env", cwd / ".workspace.env"]
    for ef in env_files:
        load_dotenv(ef)
    tiers = ["bronze", "silver", "gold"]

    options: List[Dict[str, Any]] = []

    bare_tiers = [t for t in tiers if os.getenv(f"{t.upper()}_ACCESS_KEY")]
    for tier in bare_tiers:
        bucket_name = os.getenv(f"{tier.upper()}_BUCKET", "")
        options.append({
            "label": f"default / {tier} ({bucket_name})",
            "kwargs": {"tier": tier},
            "bucket_name": bucket_name,
            "tier": tier,
        })

    for ws in _detect_workspaces():
        for tier in tiers:
            bucket_name = os.getenv(f"{ws}_BUCKET_{tier.upper()}")
            if not bucket_name:
                continue
            options.append({
                "label": f"{ws} / {tier} ({bucket_name})",
                "kwargs": {"tier": tier, "workspace": ws.lower().replace("_", "-")},
                "bucket_name": bucket_name,
                "tier": tier,
            })

    if not options:
        click.echo("No bucket env variables found (expected: BRONZE_ACCESS_KEY or {WORKSPACE}_ACCESS_KEY).")
        return

    choice = questionary.select(
        "Select a bucket to list:",
        choices=[o["label"] for o in options],
    ).ask()

    if choice is None:
        sys.exit(0)

    selected = next(o for o in options if o["label"] == choice)
    _list_bucket(selected["kwargs"], selected["tier"], selected["bucket_name"])


if __name__ == '__main__':
    main()
