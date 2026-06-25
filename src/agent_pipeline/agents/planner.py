"""Planner Agent — Layer 1: inspects data, presents a plan, dispatches workers, calls Reporter."""

from __future__ import annotations

import json
from typing import Any, Callable

from .. import state
from ..prompts.planner import SYSTEM_PROMPT
from ..providers import ANTHROPIC_MODEL_DEFAULT, OPENAI_MODEL_DEFAULT, make_provider
from ..tools import PLANNER_TOOLS, dispatch as base_dispatch

MAX_ITERATIONS = 40


def make_dispatch(
    run_id: str,
    auto_approve: bool,
    *,
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str | None,
    effort: str,
    emit_fn: Callable[..., None] | None = None,
):
    """Return a dispatch function for the Planner that handles sub-agent tool calls."""

    def _dispatch(run_id_: str, name: str, tool_input: dict[str, Any], auto_approve_: bool) -> dict[str, Any]:
        if name == "dispatch_worker":
            return _call_worker(run_id_, tool_input, auto_approve_)
        if name == "generate_report":
            return _call_reporter(run_id_, auto_approve_)
        return base_dispatch(run_id_, name, tool_input, auto_approve_)

    def _call_worker(run_id_: str, tool_input: dict[str, Any], auto_approve_: bool) -> dict[str, Any]:
        branch = tool_input.get("branch")
        sample_id = tool_input.get("sample_id", "unknown")
        input_path = tool_input.get("input_path", "")
        n_cells = tool_input.get("n_cells")
        scenario = tool_input.get("scenario")
        groups = tool_input.get("groups")
        group_column = tool_input.get("group_column")
        comparison = tool_input.get("comparison")
        paired_normal_id = tool_input.get("paired_normal_id")
        paired_normal_path = tool_input.get("paired_normal_path")

        if branch == "wes":
            from . import wes_agent
            summary = wes_agent.run(
                run_id=run_id_,
                sample_id=sample_id,
                input_path=input_path,
                scenario=scenario,
                paired_normal_id=paired_normal_id,
                paired_normal_path=paired_normal_path,
                comparison=comparison,
                provider_name=provider_name,
                api_key=api_key,
                model=model,
                base_url=base_url,
                effort=effort,
                auto_approve=auto_approve_,
                emit_fn=emit_fn,
            )
            return {"branch": "wes", "sample_id": sample_id, "status": "done", "summary": summary}

        if branch == "scrna":
            from . import scrna_agent
            summary = scrna_agent.run(
                run_id=run_id_,
                sample_id=sample_id,
                input_path=input_path,
                n_cells=n_cells,
                scenario=scenario,
                groups=groups,
                group_column=group_column,
                comparison=comparison,
                provider_name=provider_name,
                api_key=api_key,
                model=model,
                base_url=base_url,
                effort=effort,
                auto_approve=auto_approve_,
                emit_fn=emit_fn,
            )
            return {"branch": "scrna", "sample_id": sample_id, "status": "done", "summary": summary}

        return {"error": f"unknown branch '{branch}'; expected 'wes' or 'scrna'"}

    def _call_reporter(run_id_: str, auto_approve_: bool) -> dict[str, Any]:
        from . import reporter
        return reporter.run(
            run_id=run_id_,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url,
            effort=effort,
            emit_fn=emit_fn,
            auto_approve=auto_approve_,
        )

    return _dispatch


def run(
    *,
    run_id: str,
    goal: str,
    data_path: str,
    provider_name: str = "anthropic",
    api_key: str = "",
    model: str | None = None,
    base_url: str | None = None,
    effort: str = "high",
    auto_approve: bool = True,
    max_iterations: int = MAX_ITERATIONS,
) -> str:
    _model = model or (ANTHROPIC_MODEL_DEFAULT if provider_name == "anthropic" else OPENAI_MODEL_DEFAULT)

    dispatch = make_dispatch(
        run_id,
        auto_approve,
        provider_name=provider_name,
        api_key=api_key,
        model=_model,
        base_url=base_url,
        effort=effort,
    )

    provider = make_provider(
        provider_name,
        api_key=api_key,
        model=_model,
        system_prompt=SYSTEM_PROMPT,
        base_url=base_url,
        effort=effort,
        tools=PLANNER_TOOLS,
    )

    resume_summary = state.summarize_state(run_id)
    initial = (
        f"Goal: {goal}\n\n"
        f"Primary data path: {data_path}\n\n"
        f"Checkpoint state for run '{run_id}':\n{resume_summary}\n\n"
        f"Auto-proceed: yes — inspect the data, present the plan, then dispatch workers immediately.\n\n"
        "Begin by inspecting the data source."
    )
    provider.send_user_text(initial)
    state.append_log(run_id, {"event": "planner_start", "goal": goal, "data_path": data_path})

    final_text = ""
    for _ in range(max_iterations):
        result = provider.step()
        if result.thinking:
            print(f"\n[planner:thinking] {result.thinking}\n")
        if result.text:
            print(result.text)
            final_text = result.text
        if result.stop_reason != "tool_use":
            break
        tool_results = []
        for call in result.tool_calls:
            print(f"[planner:tool_use] {call['name']}({json.dumps(call['input'])})")
            try:
                output = dispatch(run_id, call["name"], call["input"], auto_approve)
                is_error = bool(isinstance(output, dict) and output.get("error"))
            except Exception as exc:  # noqa: BLE001 - surfaced to the model
                output = {"error": f"{type(exc).__name__}: {exc}"}
                is_error = True
            print(f"[planner:tool_result] {json.dumps(output, default=str)[:400]}")
            tool_results.append({
                "tool_use_id": call["id"],
                "content": json.dumps(output, default=str),
                "is_error": is_error,
            })
        provider.send_tool_results(tool_results)

    state.append_log(run_id, {"event": "planner_end", "final_text": final_text[:500]})
    return final_text
