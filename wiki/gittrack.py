
"""
Git-based auto-commit tracking for the wiki data/pages/ directory.

Ensures the pages directory is a git repository and provides helpers to
commit changes after CRUD operations. Commits are made locally; pushes
are best-effort (no remote is required).

Configuration via environment variables:
    WIKI_GIT_USER    Git author name (default: "asywiki")
    WIKI_GIT_EMAIL   Git author email (default: "asywiki@localhost")
"""

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("asywiki.gittrack")


def _git_user() -> str:
    return os.environ.get("WIKI_GIT_USER", "asywiki")


def _git_email() -> str:
    return os.environ.get("WIKI_GIT_EMAIL", "asywiki@localhost")


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in repo_dir. Returns the CompletedProcess."""
    cmd = ["git", "-C", str(repo_dir), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def ensure_repo(pages_dir: Path) -> bool:
    """
    Ensure pages_dir is a git repository.
    If not, run `git init` and configure local user.name / user.email.
    Returns True if the repo is ready (existing or newly created).
    """
    pages_dir = pages_dir.resolve()
    git_dir = pages_dir / ".git"

    if not git_dir.exists():
        log.info("Initializing git repo in %s", pages_dir)
        result = _run_git(pages_dir, "init")
        if result.returncode != 0:
            log.error("git init failed: %s", result.stderr.strip())
            return False

        # Configure local identity
        _run_git(pages_dir, "config", "user.name", _git_user())
        _run_git(pages_dir, "config", "user.email", _git_email())

        # Write .gitignore -- exclude compiled output
        gitignore = pages_dir / ".gitignore"
        gitignore.write_text("output/\n")

        # Create an initial commit if there are already files
        result = _run_git(pages_dir, "add", ".")
        if result.returncode == 0:
            result = _run_git(pages_dir, "commit", "-m", "Initial commit")
            # It's OK if there's nothing to commit (empty dir)
            if result.returncode == 0:
                log.info("Created initial commit")

    # Ensure .gitignore exists (covers repos that were already initialized
    # before this feature was added)
    gitignore = pages_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("output/\n")

    return True


def commit_all(message: str, pages_dir: Path) -> bool:
    """
    Stage everything and commit with the given message.
    Returns True if a commit was made (or nothing to commit), False on error.
    """
    pages_dir = pages_dir.resolve()
    if not (pages_dir / ".git").exists():
        if not ensure_repo(pages_dir):
            return False

    # Set identity for this invocation (covers the case where repo already
    # existed but identity wasn't set, or env changed).
    _run_git(pages_dir, "config", "user.name", _git_user())
    _run_git(pages_dir, "config", "user.email", _git_email())

    # Stage all changes
    result = _run_git(pages_dir, "add", ".")
    if result.returncode != 0:
        log.error("git add failed: %s", result.stderr.strip())
        return False

    # Commit
    result = _run_git(pages_dir, "commit", "-m", message)
    if result.returncode != 0:
        # "nothing to commit" is not an error
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            return True
        log.error("git commit failed: %s", result.stderr.strip())
        return False

    log.info("Committed: %s", message)
    return True


def push(pages_dir: Path) -> bool:
    """
    Best-effort git push. Ignores errors (no remote, auth failure, etc.).
    If GITHUB_REMOTE env var is set, ensures the remote is configured.
    Returns True if push succeeded or was skipped, False on unexpected error.
    """
    pages_dir = pages_dir.resolve()
    if not (pages_dir / ".git").exists():
        return True

    # Configure remote from GITHUB_REMOTE env var if set
    remote_url = os.environ.get("GITHUB_REMOTE", "")
    if remote_url:
        # Check if "origin" already exists
        check = _run_git(pages_dir, "remote")
        if "origin" not in check.stdout:
            _run_git(pages_dir, "remote", "add", "origin", remote_url)

    result = _run_git(pages_dir, "push")
    if result.returncode != 0:
        # Log but don't fail -- push is best-effort
        stderr = result.stderr.strip()
        log.debug("git push skipped/failed (expected if no remote): %s", stderr)
    else:
        log.info("Pushed to remote")
    return True

