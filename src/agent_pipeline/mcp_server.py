"""MCP server entry point for the bioinformatics pipeline.

Exposes four tools that let any MCP-compatible LLM client (Claude Code,
Claude Desktop, etc.) run WES / scRNA-seq analyses on local data:

  run_pipeline         — start a new analysis session (non-blocking)
  get_pipeline_status  — poll events + current status
  get_pipeline_results — fetch final report and checkpoint summary
  list_pipeline_runs   — enumerate all runs in result/

Install (from repo root):
    pip install -e .

Configure in ~/.claude/settings.json:
    { "mcpServers": { "bioinformatics": { "command": "agent-pipeline-mcp" } } }
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from mcp.server.fastmcp import FastMCP

from . import RESULT_DIR, state
from .session import AgentSession

SESSIONS: dict[str, AgentSession] = {}

DEFAULT_GOAL = (
    "Inspect the given data source, determine whether it is WES (exome) or scRNA data, "
    "and run the appropriate branch of the pipeline (QC/alignment/mutation-calling for WES, "
    "or cell-annotation/clustering/differential-expression/GSEA for scRNA), discovering any "
    "additional relevant existing data along the way."
)


def _initial_message(
    run_id: str,
    *,
    data_path: str,
    goal: str,
    study_design: str = "",
    sample_notes: str = "",
) -> str:
    parts = [f"Goal: {goal}", f"Primary data path to start from: {data_path}"]
    if study_design.strip():
        parts.append(f"Study design / cohort context:\n{study_design.strip()}")
    if sample_notes.strip():
        parts.append(f"Additional sample information:\n{sample_notes.strip()}")
    parts.append(f"Checkpoint state for run '{run_id}' so far:\n{state.summarize_state(run_id)}")
    parts.append("Begin by inspecting the data, then proceed through the appropriate branch.")
    return "\n\n".join(parts)


mcp = FastMCP(
    "bioinformatics-pipeline",
    instructions=(
        "Run WES (exome) or scRNA-seq bioinformatics analyses on local data. "
        "Workflow: call run_pipeline → poll get_pipeline_status until done → "
        "call get_pipeline_results for the final report."
    ),
)


@mcp.tool()
def run_pipeline(
    data_path: str,
    api_key: str,
    goal: str = "",
    provider: str = "anthropic",
    model: str = "",
    run_id: str = "",
    study_design: str = "",
    sample_notes: str = "",
) -> str:
    """Start a bioinformatics pipeline analysis on local data.

    Returns a run_id immediately (non-blocking). The pipeline runs in the
    background. Use get_pipeline_status to follow progress and
    get_pipeline_results to retrieve the final report.

    Supported data types (auto-detected from data_path):
      - scRNA-seq: .h5ad files, 10x matrix directories, FASTQ archives
      - WES (exome): FASTQ files / archives, BAM files
      - Multi-modal: directory with manifest.json

    Supported pipeline steps:
      scRNA: cell annotation → clustering → differential expression → GSEA
      WES:   QC (fastp) → alignment (BWA-MEM2) → variant calling (GATK4)

    Args:
        data_path: Local path to the data file or directory. Relative paths
                   are resolved from the repo root; absolute paths also work.
        api_key: LLM provider API key. Never written to disk.
        goal: Analysis goal in plain English. Leave blank to use the default
              auto-detect goal.
        provider: LLM provider — "anthropic" (default), "openai", "gemini",
                  or "grok".
        model: Model name override. Leave blank for the provider default
               (claude-opus-4-8 / gpt-4o / gemini-2.5-flash / grok-3).
        run_id: Optional run ID (auto-generated if blank). Pass an existing
                run_id with no data_path to resume from checkpoint.
        study_design: Cohort / study design description, e.g. "case-control:
                      12 tumour vs 8 normal samples".
        sample_notes: Per-sample metadata, free text.
    """
    _run_id = run_id.strip() or f"mcp-{uuid.uuid4().hex[:8]}"

    if _run_id in SESSIONS:
        session = SESSIONS[_run_id]
        return json.dumps({
            "run_id": _run_id,
            "status": session.status,
            "resumed": True,
            "message": "Reconnected to existing in-memory session.",
        })

    default_model = {
        "anthropic": "claude-opus-4-8",
        "gemini":    "gemini-2.5-flash",
        "grok":      "grok-3",
    }.get(provider, "gpt-4o")

    try:
        session = AgentSession(
            _run_id,
            provider_name=provider,
            api_key=api_key,
            model=model.strip() or default_model,
            auto_approve=True,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    SESSIONS[_run_id] = session

    checkpoint_exists = (RESULT_DIR / _run_id / "state.json").exists()
    if checkpoint_exists and not data_path.strip():
        initial = (
            f"Resume run '{_run_id}' from the existing checkpoint. "
            f"Call read_checkpoint to see what has already been completed, "
            f"then continue from where the previous session left off."
        )
        resumed = True
    else:
        initial = _initial_message(
            _run_id,
            data_path=data_path,
            goal=goal.strip() or DEFAULT_GOAL,
            study_design=study_design,
            sample_notes=sample_notes,
        )
        resumed = False

    session.post(initial)
    return json.dumps({
        "run_id": _run_id,
        "status": session.status,
        "resumed": resumed,
        "message": (
            "Pipeline started. Call get_pipeline_status to follow progress."
            if not resumed else
            "Resuming from existing checkpoint. Call get_pipeline_status."
        ),
    })


@mcp.tool()
def get_pipeline_status(run_id: str, since: int = 0) -> str:
    """Poll a running pipeline for new events and current status.

    Call this repeatedly (every 10–30 seconds) until status is
    "waiting_for_user" (pipeline paused, awaiting your input or finished)
    or "error". Pass the returned next_index as `since` on subsequent
    calls to receive only new events.

    Event types in the returned list:
      text       — model narrative output
      tool_call  — a pipeline step being invoked
      tool_result — result of a pipeline step
      thinking   — model reasoning (extended thinking, Anthropic only)
      system     — status messages (e.g. "Reporter finished")
      error      — an exception occurred

    Args:
        run_id: The run ID returned by run_pipeline.
        since: Event index to start from. Pass 0 (default) for all events,
               or the next_index from the previous call for new events only.
    """
    session = SESSIONS.get(run_id)
    if session is None:
        sp = RESULT_DIR / run_id / "state.json"
        if sp.exists():
            return json.dumps({
                "run_id": run_id,
                "status": "not_in_memory",
                "events": [],
                "next_index": 0,
                "hint": (
                    "Session not in memory — server may have restarted. "
                    "Call run_pipeline with the same run_id and no data_path to resume."
                ),
            })
        return json.dumps({"error": f"Unknown run_id '{run_id}'. Use list_pipeline_runs to see existing runs."})

    events, total = session.events_since(since)
    formatted = [
        {
            "type": e.get("type"),
            "text": (e.get("text") or e.get("name") or ""),
            "agent": e.get("agent"),
            "t": e.get("t"),
        }
        for e in events
    ]
    return json.dumps({
        "run_id": run_id,
        "status": session.status,
        "events": formatted,
        "next_index": total,
    })


@mcp.tool()
def get_pipeline_results(run_id: str) -> str:
    """Fetch the final report and checkpoint summary for a completed run.

    Call this once get_pipeline_status shows status "waiting_for_user" and
    the events include a "Reporter finished" system message. The report is
    returned as Markdown text; an HTML version with interactive Plotly
    figures is also written to disk.

    Args:
        run_id: The run ID returned by run_pipeline.
    """
    report_md_path = RESULT_DIR / run_id / "report" / "report.md"
    report_html_path = RESULT_DIR / run_id / "report" / "report.html"
    checkpoint = state.load_state(run_id)

    if not report_md_path.exists():
        steps_done = sum(1 for s in checkpoint.get("steps", []) if s.get("status") == "done")
        return json.dumps({
            "run_id": run_id,
            "report_ready": False,
            "steps_done": steps_done,
            "message": "Report not yet generated. Keep polling get_pipeline_status.",
        })

    report_text = report_md_path.read_text(encoding="utf-8")
    return json.dumps({
        "run_id": run_id,
        "report_ready": True,
        "report_markdown": report_text,
        "report_html_path": str(report_html_path) if report_html_path.exists() else None,
        "figures_dir": str(RESULT_DIR / run_id / "report" / "figures"),
        "steps_done": sum(1 for s in checkpoint.get("steps", []) if s.get("status") == "done"),
        "checkpoint_summary": state.summarize_state(run_id),
    })


@mcp.tool()
def list_pipeline_runs() -> str:
    """List all pipeline runs found in the local result/ directory.

    Returns run IDs sorted by most-recently-modified first, with creation
    time, step count, report availability, and live status for sessions
    still in memory.
    """
    if not RESULT_DIR.exists():
        return json.dumps({"runs": [], "result_dir": str(RESULT_DIR)})

    runs = []
    for d in sorted(RESULT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        sp = d / "state.json"
        if not sp.exists():
            continue
        try:
            ckpt = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        session = SESSIONS.get(d.name)
        runs.append({
            "run_id": d.name,
            "created_at": ckpt.get("created_at"),
            "steps_done": sum(1 for s in ckpt.get("steps", []) if s.get("status") == "done"),
            "report_ready": (d / "report" / "report.md").exists(),
            "live_status": session.status if session else "not_in_memory",
        })

    return json.dumps({"runs": runs, "result_dir": str(RESULT_DIR)})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
