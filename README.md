# Steve CLI

A simple CLI tool to run jobs from `jobs.yaml` with proper environment setup. Perfect for local development and testing of automation kernel jobs.

## Installation

### Install from local development

```bash
cd /home/frank/Projects/7frank/tilt-ts-4/apps/example-hehnke/packages/steve-cli
uv venv
uv pip install -e .
```

### Install from GitHub

`uv add steve-cli` 

OR

```bash
uv add git+https://github.com/your-org/tilt-ts-4.git#subdirectory=apps/example-hehnke/packages/steve-cli
```



## Usage

### Run a job

```bash
steve extract-data
steve transform-data
steve validate-data
```

This will:

1. Look for `jobs.yaml` in the current directory
2. Find the specified job by name
3. Set all environment variables from the job's `env` section
4. Execute the job's command with the proper environment

### List available jobs

```bash
steve ls
```

Shows all jobs in `jobs.yaml` with their commands, schedules, dependencies, and environment variables.

### Get help

```bash
steve help
steve --help
```

### Use custom jobs file

```bash
steve -f path/to/custom.yaml extract-data
steve ls -f path/to/custom.yaml
```

## Example

Given a `jobs.yaml` file:

```yaml
jobs:
  - name: extract-data
    cron: "0 2 * * *"
    command: ["uv", "run", "src/extract.py"]
    env:
      SOURCE_URL: "https://jsonplaceholder.typicode.com/users"
      OUTPUT_PATH: "/data/raw/extracted_data.parquet"

  - name: transform-data
    dependsOn: ["extract-data"]
    command: ["uv", "run", "src/transform.py"]
    env:
      INPUT_PATH: "/data/raw/extracted_data.parquet"
      OUTPUT_PATH: "/data/processed"
```

Running `steve extract-data` will:

1. Set `SOURCE_URL=https://jsonplaceholder.typicode.com/users`
2. Set `OUTPUT_PATH=/data/raw/extracted_data.parquet`
3. Execute: `uv run src/extract.py`

## Features

- ✅ **Simple**: Just `steve <job-name>` to run any job
- ✅ **Environment**: Automatically sets environment variables from `jobs.yaml`
- ✅ **Discovery**: Auto-finds `jobs.yaml` in current directory
- ✅ **Listing**: See all available jobs with `steve ls`
- ✅ **Flexible**: Supports custom jobs file paths
- ✅ **Colorful**: Nice colored output for better readability
- ✅ **Error handling**: Clear error messages for missing jobs or files

## Why Steve?

Named after Steve Jobs - because it helps you run **jobs** locally! 😄

## Requirements

- Python 3.8+
- click >= 8.0.0
- pyyaml >= 6.0

## Development

```bash
# Install in development mode
uv pip install -e .

# Run tests (when added)
pytest

# Format code
black steve_cli/
isort steve_cli/
```



# use cli
source .venv/bin/activate 
steve

#

uv build
uv publish
