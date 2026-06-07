from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Return value of a tool execution."""
    ok: bool
    content: str
    error: str | None = None

    @classmethod
    def success(cls, content: str) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(cls, error: str, content: str = "") -> "ToolResult":
        return cls(ok=False, content=content, error=error)


@dataclass
class Tool:
    """A callable tool available to the agent."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for tool parameters
    execute: Callable[[dict[str, Any]], ToolResult]
    #: Human-readable label shown to the LLM
    label: str | None = None

    @property
    def label_for_llm(self) -> str:
        return self.label or self.name


class ToolRegistry:
    """
    Central registry for all tools available to the SkillAgent.

    Tools are registered at startup via ``register()`` and looked up by name
    during agent tool-call loop.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning("Tool %s already registered, overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """Return JSON Schema list for all registered tools (used in LLM prompt)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with the given arguments dict."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"Unknown tool: {name}")
        try:
            return tool.execute(arguments)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool %s raised", name)
            return ToolResult.failure(str(exc))

    def list_tools(self) -> list[str]:
        """Return list of all registered tool names."""
        return list(self._tools.keys())
