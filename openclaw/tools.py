"""Deterministic tools the agent calls during analysis.

All are read-only except `append_memory`. File/git access is confined to the
configured repo path; DuckDB reads the run's Parquet snapshot. These are plain
functions so they're unit-testable without an LLM; `analyze.py` wraps them as
OpenAI tool schemas.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import duckdb

from openclaw.config import Settings


class ToolError(Exception):
    """Raised for disallowed or failed tool calls (returned to the model as an error)."""


def _confine(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting anything that escapes it."""
    root = root.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ToolError(f"path '{rel}' escapes the allowed root")
    return target


class Tools:
    def __init__(self, settings: Settings, run_dir: Path) -> None:
        self.settings = settings
        self.run_dir = run_dir
        self.repo = settings.repo_path

    # --- logs (the Parquet snapshot) ---
    def duckdb_query(self, sql: str) -> list[dict[str, Any]]:
        """Run read-only SQL over the snapshot, exposed as the view `signals`."""
        if not _is_readonly_sql(sql):
            raise ToolError("only read-only SELECT/WITH queries are allowed")
        con = duckdb.connect(database=":memory:")
        try:
            glob = str(self.run_dir / "*.parquet")
            con.execute(f"CREATE VIEW signals AS SELECT * FROM read_parquet('{glob}')")
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description]
            rows = [dict(zip(cols, r)) for r in rel.fetchmany(500)]
            return rows
        finally:
            con.close()

    # --- local codebase ---
    def read_file(self, path: str, max_bytes: int = 20_000) -> str:
        target = _confine(self.repo, path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        return target.read_text(errors="replace")[:max_bytes]

    def grep(self, pattern: str, glob: str = "") -> list[str]:
        cmd = ["git", "grep", "-n", "-I", "-e", pattern]
        if glob:
            cmd += ["--", glob]
        return self._git(cmd, allow_empty=True).splitlines()[:200]

    def git_log(self, path: str = "", limit: int = 20) -> str:
        cmd = ["git", "log", f"-{limit}", "--pretty=%h %ad %an %s", "--date=short"]
        if path:
            cmd += ["--", path]
        return self._git(cmd)

    def git_blame(self, path: str, start: int = 1, end: int = 40) -> str:
        return self._git(["git", "blame", "-L", f"{start},{end}", "--", path])

    def git_diff(self, from_ref: str, to_ref: str = "HEAD", path: str = "") -> str:
        cmd = ["git", "diff", f"{from_ref}..{to_ref}"]
        if path:
            cmd += ["--", path]
        return self._git(cmd)[:20_000]

    def list_dir(self, path: str = "") -> list[str]:
        """List files/dirs under a repo path (empty = repo root)."""
        target = _confine(self.repo, path) if path else Path(self.repo).resolve()
        if not target.exists():
            raise ToolError(f"path not found: {path}")
        entries = sorted(target.iterdir())
        return [
            str(e.relative_to(self.repo)) + ("/" if e.is_dir() else "")
            for e in entries
            if not e.name.startswith((".", "__pycache__"))
        ]

    def repo_file_tree(self, max_files: int = 80) -> str:
        """Compact source-file tree for the system prompt — shows agent real paths."""
        repo = Path(self.repo).resolve()
        exts = {".py", ".ts", ".tsx", ".js", ".go", ".java", ".rb", ".rs"}
        skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
        lines: list[str] = []
        for f in sorted(repo.rglob("*")):
            if f.suffix not in exts:
                continue
            if any(p in f.parts for p in skip):
                continue
            lines.append(str(f.relative_to(repo)))
            if len(lines) >= max_files:
                lines.append("... (truncated)")
                break
        return "\n".join(lines)

    # --- rules / constraints (you author) ---
    def read_rules(self) -> str:
        return _read_dir_text(self.settings.rules_dir)

    # --- memory (the agent grows) ---
    def read_memory(self) -> str:
        return _read_dir_text(self.settings.memory_dir)

    def append_memory(self, name: str, content: str) -> str:
        mem_dir = self.settings.memory_dir
        mem_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "note"
        path = mem_dir / f"{safe}.md"
        path.write_text(content)
        return f"wrote {path}"

    # --- helpers ---
    def _git(self, cmd: list[str], allow_empty: bool = False) -> str:
        try:
            out = subprocess.run(
                cmd, cwd=self.repo, capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError(f"git failed: {exc}") from exc
        if out.returncode != 0:
            if allow_empty and not out.stdout:
                return ""
            raise ToolError(out.stderr.strip() or f"git exited {out.returncode}")
        return out.stdout


def _read_dir_text(directory: Path, max_bytes: int = 40_000) -> str:
    if not directory.is_dir():
        return ""
    chunks: list[str] = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.suffix in {".md", ".yaml", ".yml", ".txt"}:
            chunks.append(f"### {f.relative_to(directory)}\n{f.read_text(errors='replace')}")
    return "\n\n".join(chunks)[:max_bytes]


def _is_readonly_sql(sql: str) -> bool:
    head = sql.lstrip().lower()
    if not head.startswith(("select", "with")):
        return False
    banned = ("insert", "update", "delete", "drop", "create table", "attach", "copy", "pragma")
    return not any(b in head for b in banned)
