"""Clickpost monolith checkout colocated on this machine."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)

_GIT_PULL_TIMEOUT_SECONDS = 120


class ClickpostRepoError(Exception):
    pass


def clickpost_repo_path() -> Path:
    raw = Path(settings.CLICKPOST_REPO_PATH)
    if raw.is_absolute():
        return raw
    return Path(__file__).resolve().parents[3] / raw


def require_clickpost_repo() -> Path:
    path = clickpost_repo_path()
    if not path.is_dir():
        raise ClickpostRepoError(
            f"Clickpost repo not found at {path}. Put the checkout on this "
            f"machine and set CLICKPOST_REPO_PATH to that directory."
        )
    return path


def pull_clickpost_repo(path: Path) -> str:
    """Fast-forward the colocated Clickpost checkout before locating a courier."""
    git_dir = path / ".git"
    if not git_dir.exists():
        raise ClickpostRepoError(f"Clickpost path {path} is not a git checkout")

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "pull", "origin", "master", "--ff-only"],
       
            capture_output=True,
            text=True,
            timeout=_GIT_PULL_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClickpostRepoError("git is not installed on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClickpostRepoError(
            f"Timed out after {_GIT_PULL_TIMEOUT_SECONDS}s pulling Clickpost repo at {path}"
        ) from exc

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = stderr or stdout or f"exit {result.returncode}"
        raise ClickpostRepoError(f"git pull --ff-only failed in {path}: {detail}")

    logger.info("Updated Clickpost repo at %s: %s", path, stdout or "already up to date")
    return stdout
