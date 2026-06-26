# /bioinformatics

Run a WES (exome) or scRNA-seq bioinformatics analysis using the local pipeline MCP server.

## What this skill does

1. Gathers the data path, goal, and LLM API key from the user (or $ARGUMENTS)
2. Calls `run_pipeline` to start the analysis (returns a run_id immediately)
3. Polls `get_pipeline_status` every ~20 seconds until the pipeline finishes
4. Calls `get_pipeline_results` and displays the final Markdown report inline
5. Tells the user where to find the HTML report with interactive Plotly figures

## Prerequisites

- The `bioinformatics-pipeline` MCP server must be configured:
  ```json
  // ~/.claude/settings.json
  { "mcpServers": { "bioinformatics": { "command": "agent-pipeline-mcp" } } }
  ```
- Install from the repo: `pip install -e /path/to/agent1`

---

## Instructions for Claude

The user has invoked `/bioinformatics` with arguments: **$ARGUMENTS**

Follow these steps exactly:

### Step 1 — Collect inputs

If $ARGUMENTS is empty or missing any required field, ask the user for:

- **data_path** (required): local path to the data file or directory
  - Examples: `data/demo_multimodal`, `data/scRNA_AML`, `/mnt/storage/WES/patient01`
  - Leave blank with an existing `run_id` to resume a checkpoint run
- **api_key** (required): LLM provider API key
  - Suggest checking the `ANTHROPIC_API_KEY` environment variable first; if set, use it
  - Never echo the key back in your response
- **goal** (optional): analysis goal in plain English; use the pipeline default if blank
- **provider** (optional): `anthropic` (default), `openai`, `gemini`, or `grok`
- **study_design** (optional): cohort context, e.g. "case-control: 12 tumour vs 8 normal"
- **run_id** (optional): resume an existing run

If any field was provided in $ARGUMENTS (format: `path=... key=... goal=...`), parse and use those values without asking again.

### Step 2 — Start the pipeline

Call the `run_pipeline` MCP tool with the collected inputs. Report back to the user:
- The `run_id` (they may want to save this to resume later)
- That the pipeline has started and you will now monitor it

### Step 3 — Monitor progress

Poll `get_pipeline_status(run_id=<run_id>, since=0)` and update the user on progress:

- Print a one-line summary of each meaningful event (skip repetitive tool_call/tool_result pairs unless they reveal something important)
- Highlight key milestones: data inspection complete, scenario identified, workers dispatched, steps done, reporter started, reporter finished
- Use `next_index` from each response as the `since` parameter in the next call
- Wait ~20 seconds between polls (use ScheduleWakeup or just proceed step-by-step if in interactive mode)
- Stop polling when `status == "waiting_for_user"` AND events include a system message containing "Reporter finished"
- If `status == "error"`, report the error and stop

### Step 4 — Retrieve and display results

Call `get_pipeline_results(run_id=<run_id>)`.

Display the `report_markdown` content inline (render it as Markdown).

Then tell the user:
```
Report saved to: <report_html_path>
Open in browser to view interactive Plotly figures.

Figures: <figures_dir>
Checkpoint: result/<run_id>/state.json
```

### Step 5 — Offer follow-up

Ask if the user wants to:
- Re-run with a different goal or scenario
- Ask questions about the results
- Export the reproducibility scripts (`result/<run_id>/reproduce.sh`, `Snakefile`, `methods.md`)
