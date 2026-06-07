from __future__ import annotations

from pathlib import Path

from ..subprocess_worker import run_script
from ..tool_registry import Tool, ToolResult

#: Absolute path to the arkham-rules skill directory
_ARKHAM_RULES_DIR = Path(__file__).resolve().parents[5] / "arkham-rules"
_SCRIPTS_DIR = _ARKHAM_RULES_DIR / "scripts"
_RULES_QUERY_SCRIPT = str(_SCRIPTS_DIR / "rules_query.py")


def _execute(arguments: dict) -> ToolResult:
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult.failure("query cannot be empty")

    # Try 'query' first (glossary term), fall back to 'search' (keyword across all)
    for subcmd in ("query", "search"):
        result = run_script(
            _RULES_QUERY_SCRIPT, subcmd, query,
            timeout=20.0,
            cwd=str(_ARKHAM_RULES_DIR),
        )
        # "❌" prefix means "not found" — treat as empty so we try next subcmd
        stdout = (result.stdout or "").strip()
        is_not_found = stdout.startswith("❌") or stdout.startswith("🔍") and "No" in stdout
        if result.ok and stdout and not is_not_found:
            return ToolResult.success(stdout)
        # If the script failed or returned empty/error, try the other subcommand

    # Last resort: try golden/grim/silver rule lookup if query matches one of those
    special = {"golden": "golden", "grim": "grim", "silver": "silver"}
    lowered = query.lower()
    for key, subcmd in special.items():
        if key in lowered:
            result = run_script(
                _RULES_QUERY_SCRIPT, subcmd,
                timeout=20.0,
                cwd=str(_ARKHAM_RULES_DIR),
            )
            if result.ok and result.stdout.strip():
                return ToolResult.success(result.stdout)

    return ToolResult.failure("No results found for query", content=result.stderr or "")


TOOL_SCHEMA = {
    "name": "arkham_rules",
    "description": (
        "Search the Arkham Horror LCG local rules reference. "
        "Covers glossary terms, golden/grim/silver rules, and keyword searches. "
        "Use this when the user asks about general Arkham rules or specific rule terms."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Rule term or keyword to search for. "
                    "Examples: 'forced ability', 'attack', 'golden', 'silver'"
                ),
            },
        },
        "required": ["query"],
    },
}


def build_arkham_rules_tool() -> Tool:
    return Tool(
        name="arkham_rules",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
