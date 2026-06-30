#!/usr/bin/env python3
"""
Steve CLI - A simple tool to run jobs from jobs.yaml with proper environment setup.

Usage:
    steve <job-name>    Run a job with its environment variables
    steve ls           List all available jobs
    steve help         Show this help message
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

import click
import yaml


class JobsConfig:
    """Handle parsing and querying of jobs.yaml configuration."""
    
    def __init__(self, jobs_file: Optional[Path] = None):
        self.jobs_file = jobs_file or Path.cwd() / "jobs.yaml"
        self.jobs: List[Dict[str, Any]] = []
        self._load_jobs()
    
    def _load_jobs(self) -> None:
        """Load and parse the jobs.yaml file."""
        try:
            if not self.jobs_file.exists():
                click.echo(f"❌ Error: jobs.yaml not found at {self.jobs_file}", err=True)
                click.echo("Make sure you're in a directory with a jobs.yaml file", err=True)
                sys.exit(1)
            
            with open(self.jobs_file, 'r') as f:
                data = yaml.safe_load(f)
            
            if not data or 'jobs' not in data:
                click.echo(f"❌ Error: Invalid jobs.yaml format. Expected 'jobs' key at root", err=True)
                sys.exit(1)
            
            self.jobs = data['jobs']
            
        except yaml.YAMLError as e:
            click.echo(f"❌ Error parsing jobs.yaml: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Error reading jobs.yaml: {e}", err=True)
            sys.exit(1)
    
    def get_job(self, job_name: str) -> Optional[Dict[str, Any]]:
        """Get a job by name."""
        for job in self.jobs:
            if job.get('name') == job_name:
                return job
        return None
    
    def list_jobs(self) -> List[str]:
        """Get list of all job names."""
        return [job.get('name', 'unnamed') for job in self.jobs]


def run_job_command(job: Dict[str, Any]) -> int:
    """Run a job's command with its environment variables."""
    # Get command
    command = job.get('command', [])
    if not command:
        click.echo(f"❌ Error: No command specified for job '{job.get('name')}'", err=True)
        return 1
    
    # Prepare environment
    env = os.environ.copy()
    job_env = job.get('env', {})
    
    # Convert all env values to strings and merge
    for key, value in job_env.items():
        env[key] = str(value)
    
    # Show what we're running
    job_name = job.get('name', 'unnamed')
    click.echo(f"🚀 Running job: {click.style(job_name, fg='blue', bold=True)}")
    click.echo(f"📝 Command: {click.style(' '.join(command), fg='cyan')}")
    
    if job_env:
        click.echo(f"🌍 Environment variables:")
        for key, value in job_env.items():
            click.echo(f"   {click.style(key, fg='green')}={click.style(str(value), fg='yellow')}")
    
    click.echo()  # Empty line for readability
    
    try:
        # Run the command
        result = subprocess.run(
            command,
            env=env,
            cwd=Path.cwd(),
        )
        return result.returncode
        
    except FileNotFoundError:
        click.echo(f"❌ Error: Command not found: {command[0]}", err=True)
        return 127
    except Exception as e:
        click.echo(f"❌ Error running command: {e}", err=True)
        return 1


@click.group(invoke_without_command=True)
@click.option('--jobs-file', '-f', type=click.Path(exists=True, path_type=Path), 
              help='Path to jobs.yaml file (default: ./jobs.yaml)')
@click.argument('job_name', required=False)
@click.pass_context
def main(ctx: click.Context, jobs_file: Optional[Path], job_name: Optional[str]):
    """Steve CLI - Run jobs from jobs.yaml with proper environment setup."""
    
    # Handle the case where a job name is provided directly
    if job_name:
        config = JobsConfig(jobs_file)
        
        # Special cases for built-in commands
        if job_name in ['ls', 'list']:
            list_jobs_command(config)
            return
        elif job_name in ['help', '--help', '-h']:
            click.echo(ctx.get_help())
            return
        
        # Try to run the job
        job = config.get_job(job_name)
        if not job:
            available_jobs = config.list_jobs()
            click.echo(f"❌ Error: Job '{job_name}' not found", err=True)
            if available_jobs:
                click.echo(f"Available jobs: {', '.join(available_jobs)}", err=True)
            else:
                click.echo("No jobs found in jobs.yaml", err=True)
            sys.exit(1)
        
        exit_code = run_job_command(job)
        sys.exit(exit_code)
    
    # If no job name provided, show help
    click.echo(ctx.get_help())


@main.command('ls')
@click.option('--jobs-file', '-f', type=click.Path(exists=True, path_type=Path),
              help='Path to jobs.yaml file (default: ./jobs.yaml)')
def list_command(jobs_file: Optional[Path]):
    """List all available jobs."""
    config = JobsConfig(jobs_file)
    list_jobs_command(config)


def list_jobs_command(config: JobsConfig):
    """Show list of available jobs with details."""
    jobs = config.list_jobs()
    
    if not jobs:
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
        
        # Job name
        click.echo(f"  {click.style(name, fg='blue', bold=True)}")
        
        # Command
        if command:
            click.echo(f"    Command: {click.style(' '.join(command), fg='cyan')}")
        
        # Schedule
        if cron:
            click.echo(f"    Schedule: {click.style(cron, fg='yellow')}")
        
        # Dependencies
        if depends_on:
            click.echo(f"    Depends on: {click.style(', '.join(depends_on), fg='magenta')}")
        
        # Environment variables
        if env_vars:
            click.echo(f"    Environment: {click.style(f'{len(env_vars)} variables', fg='green')}")
            for key, value in env_vars.items():
                click.echo(f"      {key}={value}")
        
        click.echo()  # Empty line between jobs


@main.command('help')
def help_command():
    """Show help information."""
    click.echo(__doc__)


if __name__ == '__main__':
    main()