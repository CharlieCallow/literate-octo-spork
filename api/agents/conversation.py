"""Ask-the-analyst conversation agent.

Lets the user chat with a specific persona about a published report. The
persona file is the system prompt (so the analyst stays in character); the
report markdown is loaded into the first user turn as context. Subsequent
turns just append to message history.

Cost-tier: same as analysts (Sonnet by default). Costs ~$0.005-0.02 per
turn depending on history length."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Conversation(Agent):
    role = "conversation"
    default_model = settings.model_sonnet

    def __init__(
        self,
        persona_path: Path,
        cost: CostTracker,
        **kwargs: object,
    ) -> None:
        super().__init__(persona_path=persona_path, cost=cost, **kwargs)  # type: ignore[arg-type]

    def reply(
        self,
        *,
        report_theme: str,
        report_markdown: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> AgentResult:
        # Build the conversation: prepend a turn that grounds the persona
        # in the published report, then append history, then the user's
        # latest message.
        primer = f"""You're answering reader questions about a Forte Research report you contributed to. Stay in your voice -- the persona file is your identity. The reader has already read the report; they're asking follow-ups. Answer in 2-4 short paragraphs, opinionated, specific. Don't restate the whole thesis -- engage with what they actually asked. If they ask something the report didn't cover, you can speculate but flag it.

REPORT THEME: {report_theme}

REPORT (so you have the context the reader sees):

{report_markdown}

Acknowledge in one short sentence that you're ready, then wait for their question."""

        messages: list[dict[str, str]] = [
            {"role": "user", "content": primer},
            {"role": "assistant", "content": "Ready."},
        ]
        for turn in history:
            role = turn.get("role")
            text = turn.get("content", "")
            if role in ("user", "assistant") and text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": user_message})

        # We don't expose tools here -- this is conversational, not research.
        # Most replies fit in 1024 tokens; bump if needed.
        return self._run_messages(messages, max_tokens=1024)

    def _run_messages(self, messages: list[dict[str, str]], *, max_tokens: int) -> AgentResult:
        """Bypass the run() loop's tool-use machinery -- conversation mode
        just sends a flat message list and returns the assistant's text."""
        from api.agents.base import AgentResult, sanitize_agent_text
        from api.agents.cost import Usage

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": self._system_blocks(""),
            "messages": messages,
        }
        resp = self._call_with_429_backoff(kwargs)
        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0),
            output_tokens=getattr(resp.usage, "output_tokens", 0),
            cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        )
        spend = self.cost.add(self.model, usage)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        self.audit("model_call", {
            "agent": self.slug, "model": self.model,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cost_usd": spend,
        })
        return AgentResult(text=sanitize_agent_text(text).strip(), cost_usd=spend)
