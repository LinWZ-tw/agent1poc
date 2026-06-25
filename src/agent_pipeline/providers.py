"""LLM provider abstraction so the agent loop isn't locked to one vendor.

`AgentSession` (session.py) only talks to this normalized interface:
`send_user_text`, `send_tool_results`, `step() -> TurnResult`. Each provider
keeps the vendor-specific message-history format internally and translates
its own response shape into a TurnResult so the rest of the loop (tool
dispatch, checkpointing, GUI events) never needs to know which vendor is
behind it.

Supported out of the box:
  - "anthropic": Claude, via the Messages API (adaptive thinking included).
  - "openai": OpenAI's Chat Completions API -- and, via `base_url`, any
    OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, Groq, Together,
    Azure OpenAI, etc.), which covers most "I want to use a different LLM"
    requests without writing a new provider per vendor.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

from .tools import TOOLS

ANTHROPIC_MODEL_DEFAULT = "claude-opus-4-8"
OPENAI_MODEL_DEFAULT = "gpt-4o"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"
GROK_MODEL_DEFAULT = "grok-3"

# Fixed base URLs for providers that use the OpenAI-compatible wire format
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GROK_BASE_URL = "https://api.x.ai/v1"

# Fallback model lists — tried in order when the primary returns 503 / 429 / 529.
# The user's chosen model is always prepended first by _build_fallbacks().
# Gemini 2.0 was shut down 2026-06-01; current generation is 2.5.
_ANTHROPIC_FALLBACKS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
_GEMINI_FALLBACKS    = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
_GROK_FALLBACKS      = ["grok-3", "grok-2"]
_OPENAI_FALLBACKS    = ["gpt-4o", "gpt-4o-mini"]

# HTTP status codes that indicate transient overload (worth retrying with a fallback model)
_OVERLOAD_CODES = {429, 503, 529}


def _build_fallbacks(chosen: str, defaults: list[str]) -> list[str]:
    """User's chosen model first, then the standard list (no duplicates)."""
    return [chosen] + [m for m in defaults if m != chosen]


@dataclass
class TurnResult:
    stop_reason: str  # "tool_use" or "end_turn"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, input}]
    text: str = ""
    thinking: str = ""
    warning: str = ""  # set when a fallback model was used


class AnthropicProvider:
    """Wraps the same Messages API call orchestrator.py uses, normalized."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = ANTHROPIC_MODEL_DEFAULT,
        system_prompt: str = "",
        effort: str = "high",
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        model_fallbacks: list[str] | None = None,
    ) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        self.messages: list[dict[str, Any]] = []
        self._tools = tools if tools is not None else TOOLS
        self._fallbacks = model_fallbacks or _build_fallbacks(model, _ANTHROPIC_FALLBACKS)

    def send_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": [{"type": "text", "text": text}]})

    def send_tool_results(self, results: list[dict[str, Any]]) -> None:
        content = [
            {
                "type": "tool_result",
                "tool_use_id": r["tool_use_id"],
                "content": r["content"],
                "is_error": r.get("is_error", False),
            }
            for r in results
        ]
        self.messages.append({"role": "user", "content": content})

    @staticmethod
    def _block_to_dict(block: Any) -> dict[str, Any]:
        """Serialize an Anthropic SDK content block to a plain dict.

        Appending raw SDK Pydantic objects back to messages works today but is
        fragile — the SDK may reject its own model instances in future versions,
        and ThinkingBlock.signature must be round-tripped exactly or the API
        rejects the turn. Convert eagerly to avoid both problems.
        """
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        if block.type == "thinking":
            d: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
            if sig := getattr(block, "signature", None):
                d["signature"] = sig
            return d
        # Unknown future block type — best-effort via model_dump
        try:
            return block.model_dump()
        except Exception:  # noqa: BLE001
            return {"type": block.type}

    def step(self) -> TurnResult:
        import anthropic as _anthropic

        last_exc: Exception | None = None
        for model in self._fallbacks:
            try:
                self.model = model
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    thinking={"type": "adaptive", "display": "summarized"},
                    output_config={"effort": self.effort},
                    system=self.system,
                    tools=self._tools,
                    messages=self.messages,
                )
            except _anthropic.APIStatusError as exc:
                if exc.status_code in _OVERLOAD_CODES:
                    print(f"[provider] {model} returned {exc.status_code} — trying next fallback", file=sys.stderr)
                    last_exc = exc
                    continue
                raise
            else:
                break
        else:
            raise last_exc  # type: ignore[misc]

        fallback_warning = f"Model {self._fallbacks[0]} unavailable — switched to {self.model}" if self.model != self._fallbacks[0] else ""
        self.messages.append({"role": "assistant", "content": [self._block_to_dict(b) for b in response.content]})

        tool_calls, text_parts, thinking_parts = [], [], []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
            elif block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking" and block.thinking:
                thinking_parts.append(block.thinking)

        stop_reason = "tool_use" if response.stop_reason == "tool_use" else "end_turn"
        return TurnResult(
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            text="".join(text_parts),
            thinking="\n".join(thinking_parts),
            warning=fallback_warning,
        )


class OpenAIProvider:
    """OpenAI's Chat Completions API, and anything that speaks the same
    wire format via `base_url` (Ollama, vLLM, LM Studio, Groq, Together,
    Azure OpenAI, ...). No "thinking" concept here -- left blank."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = OPENAI_MODEL_DEFAULT,
        system_prompt: str = "",
        base_url: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        model_fallbacks: list[str] | None = None,
        **_ignored: Any,
    ) -> None:
        import openai

        # Local/self-hosted OpenAI-compatible servers often don't check the
        # key at all; still require *something* non-empty per the SDK.
        self.client = openai.OpenAI(api_key=api_key or "not-needed", base_url=base_url or None)
        self.model = model
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        _tools = tools if tools is not None else TOOLS
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in _tools
        ]
        self._fallbacks = model_fallbacks or [model]

    def send_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def send_tool_results(self, results: list[dict[str, Any]]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r["tool_use_id"], "content": r["content"]})

    def step(self) -> TurnResult:
        import openai as _openai

        last_exc: Exception | None = None
        for model in self._fallbacks:
            try:
                self.model = model
                response = self.client.chat.completions.create(model=self.model, messages=self.messages, tools=self.tools)
            except _openai.APIStatusError as exc:
                if exc.status_code in _OVERLOAD_CODES:
                    print(f"[provider] {model} returned {exc.status_code} — trying next fallback", file=sys.stderr)
                    last_exc = exc
                    continue
                raise
            else:
                break
        else:
            raise last_exc  # type: ignore[misc]

        fallback_warning = f"Model {self._fallbacks[0]} unavailable — switched to {self.model}" if self.model != self._fallbacks[0] else ""
        msg = response.choices[0].message

        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.content}
        tool_calls: list[dict[str, Any]] = []
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})
        self.messages.append(assistant_entry)

        stop_reason = "tool_use" if tool_calls else "end_turn"
        return TurnResult(stop_reason=stop_reason, tool_calls=tool_calls, text=msg.content or "", thinking="", warning=fallback_warning)


def make_provider(
    provider_name: str,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    base_url: str | None = None,
    effort: str = "high",
    tools: list[dict[str, Any]] | None = None,
):
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model, system_prompt=system_prompt, effort=effort, tools=tools,
                                 model_fallbacks=_build_fallbacks(model, _ANTHROPIC_FALLBACKS))
    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key, model=model, system_prompt=system_prompt, base_url=base_url, tools=tools,
                              model_fallbacks=_build_fallbacks(model, _OPENAI_FALLBACKS))
    if provider_name == "gemini":
        return OpenAIProvider(api_key=api_key, model=model, system_prompt=system_prompt, base_url=_GEMINI_BASE_URL, tools=tools,
                              model_fallbacks=_build_fallbacks(model, _GEMINI_FALLBACKS))
    if provider_name == "grok":
        return OpenAIProvider(api_key=api_key, model=model, system_prompt=system_prompt, base_url=_GROK_BASE_URL, tools=tools,
                              model_fallbacks=_build_fallbacks(model, _GROK_FALLBACKS))
    raise ValueError(f"unknown provider '{provider_name}', expected 'anthropic', 'openai', 'gemini', or 'grok'")
