"""Fix-as-PR: write agent-proposed file fixes onto a new local git branch.

Only wired in when the user passes --apply. Writes are confined to the repo, land
on a fresh `openclaw/fix-<run>` branch, and are committed locally (never pushed) —
the human reviews and opens the PR. This is the only mutating capability.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from openclaw.tools import ToolError, _confine

log = structlog.get_logger()


def _git(repo: Path, args: list[str]) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=30, check=False
    )
    if out.returncode != 0:
        raise ToolError(out.stderr.strip() or f"git {' '.join(args)} exited {out.returncode}")
    return out.stdout


class FixWriter:
    """Accumulates proposed file writes on a dedicated branch, commits at the end."""

    def __init__(self, repo: Path, run_id: str) -> None:
        self.repo = repo
        self.branch = f"openclaw/fix-{run_id}"
        self.files: list[str] = []

    def write_fix(self, path: str, content: str) -> str:
        target = _confine(self.repo, path)
        if not self.files:
            _git(self.repo, ["checkout", "-b", self.branch])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.files.append(path)
        log.info("fix_written", path=path, branch=self.branch)
        return f"wrote {path} on branch {self.branch} (review before pushing)"

    def commit(self, message: str) -> str | None:
        if not self.files:
            return None
        _git(self.repo, ["add", *self.files])
        _git(self.repo, ["commit", "-m", message])
        summary = f"Committed {len(self.files)} file(s) to branch {self.branch} (not pushed)."
        log.info("fix_committed", branch=self.branch, files=len(self.files))
        return summary
