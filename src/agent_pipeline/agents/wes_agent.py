"""WES Worker Agent — Layer 2: executes QC → alignment → mutation_calling."""

from __future__ import annotations

import json
from typing import Any, Callable

from .. import state
from ..prompts.wes import SYSTEM_PROMPT
from ..providers import ANTHROPIC_MODEL_DEFAULT, OPENAI_MODEL_DEFAULT, make_provider
from ..tools import WORKER_TOOLS, dispatch

MAX_ITERATIONS = 30
_LABEL = "WES Worker"


def run(
    *,
    run_id: str,
    sample_id: str,
    input_path: str,
    scenario: str | None = None,
    paired_normal_id: str | None = None,
    paired_normal_path: str | None = None,
    comparison: str | None = None,
    provider_name: str = "anthropic",
    api_key: str = "",
    model: str | None = None,
    base_url: str | None = None,
    effort: str = "high",
    auto_approve: bool = True,
    emit_fn: Callable[..., None] | None = None,
) -> str:
    _model = model or (ANTHROPIC_MODEL_DEFAULT if provider_name == "anthropic" else OPENAI_MODEL_DEFAULT)
    _emit = emit_fn or (lambda **_: None)

    provider = make_provider(
        provider_name,
        api_key=api_key,
        model=_model,
        system_prompt=SYSTEM_PROMPT,
        base_url=base_url,
        effort=effort,
        tools=WORKER_TOOLS,
    )

    scenario_lines: list[str] = []
    if scenario:
        scenario_lines.append(f"scenario: {scenario}")
    if paired_normal_id:
        scenario_lines.append(f"paired_normal_id: {paired_normal_id}")
    if paired_normal_path:
        scenario_lines.append(f"paired_normal_path: {paired_normal_path}")
    if comparison:
        scenario_lines.append(f"comparison: {comparison}")
    scenario_hint = ("\n" + "\n".join(scenario_lines)) if scenario_lines else ""

    initial = (
        f"Run the WES pipeline branch.\n"
        f"run_id: {run_id}\n"
        f"sample_id: {sample_id}\n"
        f"input_path: {input_path}"
        f"{scenario_hint}\n\n"
        f"Follow your system prompt's scenario-specific instructions. "
        f"Use mode='mock'. Check read_checkpoint first for any already-completed steps."
    )
    provider.send_user_text(initial)
    state.append_log(run_id, {"event": "wes_agent_start", "sample_id": sample_id, "input_path": input_path})
    _emit(type="system", text=f"WES Worker started — sample: {sample_id}", agent=_LABEL)

    final_text = ""
    for _ in range(MAX_ITERATIONS):
        result = provider.step()
        if result.thinking:
            _emit(type="thinking", text=result.thinking, agent=_LABEL)
        if result.text:
            final_text = result.text
            _emit(type="text", text=result.text, agent=_LABEL)
        if result.stop_reason != "tool_use":
            break
        tool_results: list[dict[str, Any]] = []
        for call in result.tool_calls:
            _emit(type="tool_call", name=call["name"], input=call["input"], agent=_LABEL)
            try:
                output = dispatch(run_id, call["name"], call["input"], auto_approve)
                is_error = bool(isinstance(output, dict) and output.get("error"))
            except Exception as exc:  # noqa: BLE001 - surfaced to the model
                output = {"error": f"{type(exc).__name__}: {exc}"}
                is_error = True
            _emit(type="tool_result", name=call["name"], output=output, is_error=is_error, agent=_LABEL)
            tool_results.append({
                "tool_use_id": call["id"],
                "content": json.dumps(output, default=str),
                "is_error": is_error,
            })
        provider.send_tool_results(tool_results)

    state.append_log(run_id, {"event": "wes_agent_end", "summary": final_text[:500]})
    _emit(type="system", text=f"WES Worker finished — sample: {sample_id}", agent=_LABEL)
    return final_text
