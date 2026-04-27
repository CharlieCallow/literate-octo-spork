"""Agent base class. Loads a persona markdown file and runs a Messages-API loop
with tool use. Tracks cost per call and surfaces every interaction to the caller
via an audit hook."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anthropic import Anthropic, RateLimitError

from api.agents.cost import CostTracker, Usage
from api.settings import settings

log = logging.getLogger("agent")

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
class Citation:
    url: str
    title: str | None = None
    source: str = "web"  # web | wikipedia_summary | edgar_filings | etc.


@dataclass
class AgentResult:
    text: str
    cost_usd: float
    citations: list[Citation] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)


def _extract_server_citations(content_blocks: Any, into: list[Citation]) -> None:
    """Pull URLs out of web_search_tool_result blocks in a model response."""
    for block in content_blocks:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        for r in getattr(block, "content", []) or []:
            url = getattr(r, "url", None)
            if url:
                into.append(Citation(
                    url=url,
                    title=getattr(r, "title", None),
                    source="web",
                ))


def _extract_local_citations(tool_name: str, out: Any, into: list[Citation]) -> None:
    """Parse a local tool's JSON output for url-like fields and append Citations.

    Handles:
      - top-level `url` (Wikipedia)
      - `filings[*].url` (EDGAR)
      - `posts[*].permalink` (Reddit)
      - `stories[*].url` and `stories[*].hn_url` (HN Algolia)
    """
    try:
        data = json.loads(out) if isinstance(out, str) else out
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return

    if url := data.get("url"):
        into.append(Citation(url=url, title=data.get("title"), source=tool_name))

    for f in data.get("filings", []) or []:
        if isinstance(f, dict) and (u := f.get("url")):
            into.append(Citation(url=u, title=f.get("form"), source=tool_name))

    for p in data.get("posts", []) or []:
        if isinstance(p, dict) and (u := p.get("permalink") or p.get("url")):
            into.append(Citation(url=u, title=p.get("title"), source=tool_name))

    for s in data.get("stories", []) or []:
        if not isinstance(s, dict):
            continue
        # HN stories have both an external `url` and a `hn_url` discussion link.
        # The external URL is more useful for citation; fall back to the HN page.
        if u := (s.get("url") or s.get("hn_url")):
            into.append(Citation(url=u, title=s.get("title"), source=tool_name))


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
        # max_retries=5 lets the SDK ride out transient 429s with its own
        # backoff. We add an outer wrapper for 429s that need a longer wait
        # (per-minute token limits don't clear inside the SDK's window).
        self._client = Anthropic(api_key=settings.anthropic_api_key, max_retries=5)

    @property
    def slug(self) -> str:
        return self.persona_path.stem

    @property
    def display_name(self) -> str:
        text = self.persona_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.slug

    def _system_prompt(self, extra: str = "") -> str:
        persona = self.persona_path.read_text(encoding="utf-8")
        return persona + ("\n\n---\n\n" + extra if extra else "")

    def run(
        self,
        user_prompt: str,
        *,
        tools: list[Tool] | None = None,
        server_tools: list[dict[str, Any]] | None = None,
        extra_system: str = "",
        max_iters: int = 6,
        max_tokens: int = 4096,
    ) -> AgentResult:
        # Local tools have a Python callable we resolve by name; server tools
        # (e.g. Anthropic's web_search_20250305) are executed by the API and
        # the result comes back inline -- we just pass the config through.
        tools = tools or []
        server_tools = server_tools or []
        tool_map = {t.name: t for t in tools}
        anth_tools: list[dict[str, Any]] = [t.to_anthropic() for t in tools] + list(server_tools)

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        transcript: list[dict[str, Any]] = []
        citations: list[Citation] = []
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

            resp = self._call_with_429_backoff(kwargs)

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
            _extract_server_citations(resp.content, citations)

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
                return AgentResult(
                    text=text.strip(),
                    cost_usd=total_cost,
                    citations=citations,
                    transcript=transcript,
                )

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
                    _extract_local_citations(block.name, out, citations)
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
            citations=citations,
            transcript=transcript,
        )

    def _call_with_429_backoff(self, kwargs: dict[str, Any]) -> Any:
        """Call messages.create() and ride out per-minute rate-limit windows.

        The Anthropic SDK retries on 429 internally, but its backoff window is
        short. Per-minute-token limits often need a full bucket reset (~60s);
        we honour the retry-after header (capped at 65s) for up to 4 attempts."""
        attempts = 4
        for i in range(attempts):
            try:
                return self._client.messages.create(**kwargs)
            except RateLimitError as e:
                if i == attempts - 1:
                    raise
                wait = 35
                resp = getattr(e, "response", None)
                if resp is not None:
                    ra = resp.headers.get("retry-after")
                    if ra:
                        with contextlib.suppress(ValueError):
                            wait = min(int(ra), 65)
                log.warning(
                    "agent=%s rate-limited; sleeping %ss (attempt %d/%d)",
                    self.slug, wait, i + 1, attempts,
                )
                self.audit("rate_limit", {"agent": self.slug, "wait_s": wait, "attempt": i + 1})
                time.sleep(wait)
        raise RuntimeError("unreachable")  # pragma: no cover
