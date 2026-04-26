"""Agent base class. Loads a persona markdown file and runs a Messages-API loop
with tool use. Tracks cost per call and surfaces every interaction to the caller
via an audit hook."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from api.agents.cost import CostTracker, Usage
from api.settings import settings

AuditHook = Callable[[str, dict[str, Any]], None]
ToolFn = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn

    def to_anthropic(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class AgentResult:
    text: str
    cost_usd: float
    transcript: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    """Generic persona-driven agent. Concrete agents subclass to set defaults."""

    role: str = "agent"
    default_model: str = "claude-sonnet-4-6"

    def __init__(
        self,
        persona_path: Path,
        cost: CostTracker,
        *,
        model: str | None = None,
        audit: AuditHook | None = None,
    ) -> None:
        self.persona_path = persona_path
        self.cost = cost
        self.model = model or self.default_model
        self.audit = audit or (lambda _e, _d: None)
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    @property
    def slug(self) -> str:
        return self.persona_path.stem

    @property
    def display_name(self) -> str:
        text = self.persona_path.read_text()
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.slug

    def _system_prompt(self, extra: str = "") -> str:
        persona = self.persona_path.read_text()
        return persona + ("\n\n---\n\n" + extra if extra else "")

    def run(
        self,
        user_prompt: str,
        *,
        tools: list[Tool] | None = None,
        extra_system: str = "",
        max_iters: int = 6,
        max_tokens: int = 4096,
    ) -> AgentResult:
        tools = tools or []
        tool_map = {t.name: t for t in tools}
        anth_tools = [t.to_anthropic() for t in tools] or None

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        transcript: list[dict[str, Any]] = []
        total_cost = 0.0

        for _ in range(max_iters):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": self._system_prompt(extra_system),
                "messages": messages,
            }
            if anth_tools:
                kwargs["tools"] = anth_tools

            resp = self._client.messages.create(**kwargs)

            usage = Usage(
                input_tokens=getattr(resp.usage, "input_tokens", 0),
                output_tokens=getattr(resp.usage, "output_tokens", 0),
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            )
            spend = self.cost.add(self.model, usage)
            total_cost += spend
            self.audit("model_call", {
                "agent": self.slug, "model": self.model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "cost_usd": spend,
            })

            messages.append({"role": "assistant", "content": resp.content})
            transcript.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
                return AgentResult(text=text.strip(), cost_usd=total_cost, transcript=transcript)

            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool = tool_map.get(block.name)
                if tool is None:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": f"Unknown tool: {block.name}", "is_error": True,
                    })
                    continue
                try:
                    out = tool.fn(**(block.input or {}))
                    self.audit("tool_call", {"agent": self.slug, "tool": block.name, "input": block.input})
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": str(out),
                    })
                except Exception as e:  # noqa: BLE001
                    self.audit("tool_error", {"agent": self.slug, "tool": block.name, "error": str(e)})
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": f"Tool error: {e}", "is_error": True,
                    })

            messages.append({"role": "user", "content": tool_results})

        # Loop exhausted without a final answer.
        return AgentResult(
            text="[agent halted: max iterations reached]",
            cost_usd=total_cost,
            transcript=transcript,
        )
