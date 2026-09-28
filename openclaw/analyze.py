"""The analyze step: run the OpenAI agent over a snapshot + local code/rules/memory.

Builds the tool surface from `Tools`, injects rules/constraints/memory into the
system prompt, runs the tool loop, saves a report, and lets the agent append what
it learned to `memory/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from openclaw import jev
from openclaw.apply import FixWriter
from openclaw.config import Settings
from openclaw.llm import run_tool_loop
from openclaw.report import save_report
from openclaw.tools import Tools

log = structlog.get_logger()

# OpenAI Responses-API function-tool schemas (flat: type/name/description/parameters).
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "duckdb_query",
        "description": "Run read-only SQL over the log snapshot (view `signals`: "
        "source, kind, severity, ts, entity, message, raw_json).",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a file from the local repo (path relative to the repo root).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "grep",
        "description": "git grep the repo for a regex; optional pathspec glob.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "type": "function",
        "name": "git_log",
        "description": "Recent commits (optionally for a path) — did something change recently?",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        },
    },
    {
        "type": "function",
        "name": "git_blame",
        "description": "Blame a line range of a file — who/when/why a line changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "git_diff",
        "description": "Diff between two refs (default to HEAD) — what changed between deploys.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_ref": {"type": "string"},
                "to_ref": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["from_ref"],
        },
    },
    {
        "type": "function",
        "name": "list_dir",
        "description": "List files and subdirectories under a repo path. Use this to discover real paths before calling grep/read_file/git_log.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path inside the repo (empty = root)"}},
        },
    },
    {
        "type": "function",
        "name": "append_memory",
        "description": "Save a durable learning for future runs (one fact per note).",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
            "required": ["name", "content"],
        },
    },
]

SYSTEM = """You are OpenClaw, an on-demand DevSecOps analyst.

You are given a snapshot of logs (query it via `duckdb_query`, view `signals`) plus
read-only access to the service's local codebase and git history. Your job:
1. Find the most important issue(s) in the snapshot.
2. Use the code + git tools to decide whether each is a BUG or INTENTIONAL
   (check git_blame/git_log/git_diff and compare to the rules/design below).
3. Propose a concrete, grounded fix — reference exact files/lines. Do NOT invent code.

Rules:
- Ground every claim in evidence: a log row (from duckdb_query) or a code/git reference.
  If evidence is insufficient, say so — do not fabricate.
- Respect the constraints below (e.g. never recommend exceeding max_replicas).
- {apply_note}
- When you learn a durable fact (a memory limit, an owner, a recurring pattern,
  a resolved incident + its fix), call append_memory so future runs skip the rediscovery.
- Finish with a Markdown report: ## Summary, ## Findings (each with evidence +
  bug-vs-intentional + proposed fix), ## Recommendations.
- For grep/git_log/git_blame/read_file: use ONLY paths that appear in the REPO FILE TREE
  below. Never guess paths — list_dir first if unsure.

--- REPO FILE TREE (Python/JS source only, top 80 files) ---
{file_tree}

--- RULES / RUNBOOKS (you author these) ---
{rules}

--- MEMORY (learned from past runs) ---
{memory}
"""


WRITE_FIX_TOOL = {
    "type": "function",
    "name": "write_fix",
    "description": "Write a proposed fix (full new file content) onto a review branch. "
    "Only for concrete, evidence-backed fixes — never speculative.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}


def make_dispatch(tools: Tools, fixer: FixWriter | None = None):
    table = {
        "duckdb_query": lambda a: tools.duckdb_query(a["sql"]),
        "read_file": lambda a: tools.read_file(a["path"]),
        "grep": lambda a: tools.grep(a["pattern"], a.get("glob", "")),
        "list_dir": lambda a: tools.list_dir(a.get("path", "")),
        "git_log": lambda a: tools.git_log(a.get("path", ""), a.get("limit", 20)),
        "git_blame": lambda a: tools.git_blame(a["path"], a.get("start", 1), a.get("end", 40)),
        "git_diff": lambda a: tools.git_diff(a["from_ref"], a.get("to_ref", "HEAD"), a.get("path", "")),
        "append_memory": lambda a: tools.append_memory(a["name"], a["content"]),
    }
    if fixer is not None:
        table["write_fix"] = lambda a: fixer.write_fix(a["path"], a["content"])

    def dispatch(name: str, args: dict[str, Any]) -> Any:
        if name not in table:
            return f"ERROR: unknown tool {name}"
        return table[name](args)

    return dispatch


def _maybe_trace(client: Any, settings: Settings) -> Any:
    """Wrap the OpenAI client with LangSmith tracing if a key is configured."""
    import os

    if not os.getenv("LANGSMITH_API_KEY"):
        return client
    try:
        from langsmith.wrappers import wrap_openai

        log.info("langsmith_enabled")
        return wrap_openai(client)
    except ImportError:
        log.warning("langsmith_skipped", reason="pip install langsmith")
        return client


def run_analysis(run_dir: Path, settings: Settings, apply: bool = False) -> str:
    from openai import OpenAI  # imported lazily so snapshot-only runs need no key

    client = _maybe_trace(OpenAI(api_key=settings.openai_api_key), settings)
    tools = Tools(settings, run_dir)

    # System-1 pre-rank: focus the expensive model on the clusters that matter.
    focus = jev.prerank(settings, jev.cluster_summary(tools))
    focus_txt = "\n".join(
        f"- {c['severity']} {c['source']}/{c['entity']} (n={c['n']}): {c['sample']}" for c in focus
    )

    fixer = FixWriter(settings.repo_path, run_dir.name) if apply else None
    tool_schemas = [*TOOL_SCHEMAS, WRITE_FIX_TOOL] if apply else TOOL_SCHEMAS

    system = SYSTEM.format(
        file_tree=tools.repo_file_tree(),
        rules=tools.read_rules() or "(none)",
        memory=tools.read_memory() or "(none yet)",
        apply_note=(
            "You MAY call write_fix to stage a concrete fix on a review branch."
            if apply
            else "You propose fixes only; do not attempt to modify files."
        ),
    )
    user = (
        f"Analyze the snapshot at {run_dir}. Focus areas (pre-ranked):\n{focus_txt}\n\n"
        "Summarize severities via duckdb_query, then investigate the worst issue end-to-end."
    )

    log.info("analysis_start", model=settings.openai_model, run_dir=str(run_dir), apply=apply)
    report = run_tool_loop(
        client, settings.openai_model, system, user, tool_schemas, make_dispatch(tools, fixer)
    )
    if fixer is not None:
        committed = fixer.commit(f"OpenClaw proposed fix ({run_dir.name})")
        if committed:
            report += f"\n\n---\n{committed}"
    path = save_report(run_dir, report)
    log.info("analysis_done", report=str(path))
    return report
