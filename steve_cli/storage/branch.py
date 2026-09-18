from __future__ import annotations

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_MAIN_BRANCHES = {"main", "master"}


def _sanitize(branch: str) -> str:
    branch = branch.lower().strip()
    branch = re.sub(r"[^a-z0-9]+", "_", branch)
    return branch.strip("_") or "main"


def get_branch_prefix() -> str:
    for env_var in ("STEVE_BRANCH", "GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        value = os.getenv(env_var, "").strip()
        if value:
            return _sanitize(value)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return _sanitize(branch)
    except Exception as exc:
        logger.debug("git branch detection failed: %s", exc)

    logger.warning("Could not detect git branch; defaulting to 'main'")
    return "main"
