"""Manual Claude tool-use loop driving the pipeline agent."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from . import state
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS, dispatch

MODEL = "claude-opus-4-8"


def _block_to_log(block: Any) -> dict[str, Any]:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {"type": block.type}


def run(
    *,
    run_id: str,
    goal: str,
    data_path: str,
    auto_approve: bool = True,
    effort: str = "high",
    max_iterations: int = 40,
    max_tokens: int = 8192,
) -> str:
    client = anthropic.Anthropic()

    resume_summary = state.summarize_state(run_id)
    user_prompt = (
        f"Goal: {goal}\n\n"
        f"Primary data path to start from: {data_path}\n\n"
        f"Checkpoint state for run '{run_id}' so far:\n{resume_summary}\n\n"
        "Begin by inspecting the data, then proceed through the appropriate branch."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    state.append_log(run_id, {"event": "run_start", "goal": goal, "data_path": data_path})

    final_text = ""
    for iteration in range(max_iterations):
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )

        state.append_log(
            run_id,
            {
                "event": "model_turn",
                "iteration": iteration,
                "stop_reason": response.stop_reason,
                "content": [_block_to_log(b) for b in response.content],
                "usage": response.usage.model_dump() if response.usage else None,
            },
        )

        for block in response.content:
            if block.type == "thinking" and block.thinking:
                print(f"\n[thinking] {block.thinking}\n")
            elif block.type == "text":
                print(block.text)
                final_text = block.text

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[tool_use] {block.name}({json.dumps(block.input)})")
            try:
                result = dispatch(run_id, block.name, block.input, auto_approve)
                is_error = bool(isinstance(result, dict) and result.get("error"))
            except Exception as exc:  # noqa: BLE001 - surfaced back to the model, not raised
                result = {"error": f"{type(exc).__name__}: {exc}"}
                is_error = True
            print(f"[tool_result] {json.dumps(result, default=str)[:500]}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})
    else:
        state.append_log(run_id, {"event": "max_iterations_reached", "max_iterations": max_iterations})

    state.append_log(run_id, {"event": "run_end", "final_text": final_text})
    return final_text
