#!/usr/bin/env python3
"""
Steve CLI - A simple tool to run jobs from jobs.yaml with proper environment setup.

Usage:
    steve jobs ls              List all available jobs
    steve jobs run <job-name>  Run a job with its environment variables
    steve setup all            Decrypt SOPS-encoded .env files
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

import click
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


@main.group()
def jobs():
    """Manage and run jobs from jobs.yaml."""
    pass


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


SETUP_IGNORE = [".env.example", ".env.template"]


@main.group()
def setup():
    """Setup commands for environment configuration."""
    pass


@setup.command("env")
def setup_env():
    """Decrypt SOPS-encoded .env files in current directory and write plaintext .env files."""
    cwd = Path.cwd()
    found: List[Path] = []
    for pattern in ["*.enc.env", "*.encoded.env"]:
        found.extend(sorted(cwd.glob(pattern)))

    found = [f for f in found if f.name not in SETUP_IGNORE]

    if not found:
        click.echo("No *.enc.env or *.encoded.env files found.")
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

        stem = enc_file.name.replace(".encoded.env", "").replace(".enc.env", "")
        out_file = cwd / f"{stem}.env"
        out_file.write_text(result.stdout)

        click.echo(f"   ✅ Decrypted -> {click.style(out_file.name, fg='cyan')}")
        click.echo(f"   💡 Run: {click.style(f'source {out_file.name}', fg='yellow')}")


if __name__ == '__main__':
    main()
