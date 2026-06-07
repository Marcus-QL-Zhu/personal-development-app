from __future__ import annotations

from pathlib import Path

from ..subprocess_worker import run_script
from ..tool_registry import Tool, ToolResult

#: Absolute path to the arkham-rules skill directory
_ARKHAM_RULES_DIR = Path(__file__).resolve().parents[5] / "arkham-rules"
_SCRIPTS_DIR = _ARKHAM_RULES_DIR / "scripts"
_WEB_FAQ_SCRIPT = str(_SCRIPTS_DIR / "web_faq_query.py")


def _execute(arguments: dict) -> ToolResult:
    card_code = arguments.get("card_code", "").strip()
    if not card_code:
        return ToolResult.failure("card_code cannot be empty")

    # web_faq_query.py takes card code (e.g. "10050") and outputs formatted FAQ
    result = run_script(
        _WEB_FAQ_SCRIPT, card_code,
        timeout=15.0,
    )

    if result.timed_out:
        return ToolResult.failure("web FAQ query timed out after 15s")
    if result.returncode != 0:
        return ToolResult.failure(f"web FAQ query failed: {result.stderr or result.stdout}")
    return ToolResult.success(result.stdout)


TOOL_SCHEMA = {
    "name": "web_faq",
    "description": (
        "Fetch the latest official FAQ from ArkhamDB web page for a specific card. "
        "Queries arkhamdb.com/card/{code} and extracts official FAQ entries. "
        "Use this when you need the most up-to-date official ruling for a card, "
        "or when local FAQ data does not have the answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "card_code": {
                "type": "string",
                "description": (
                    "ArkhamDB card code (numeric string, e.g. '10050' for Transmogrify, "
                    "'01001' for Roland Banks). Can be 5 digits. "
                    "If you only have the card name, use arkham_cards tool first to find the code."
                ),
            },
        },
        "required": ["card_code"],
    },
}


def build_web_faq_tool() -> Tool:
    return Tool(
        name="web_faq",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
