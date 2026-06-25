# Project TODO

Status audit against the target architecture described in `README.md`.

---

## Done

The foundation layer is complete and functional.

- **`steps/`** — all 8 pipeline step modules implemented with mock + real modes:
  `detect.py`, `qc.py`, `alignment.py`, `mutation.py`, `annotation.py`,
  `clustering.py`, `diffexp.py`, `gsea.py`
- **`jobs.py`** — background job queue (`ThreadPoolExecutor`); start/poll/result
  contract works identically in mock and real mode
- **`state.py`** — checkpoint persistence (`state.json` + `agent_log.jsonl`);
  thread-safe; resumes cleanly across restarts; `report_dir()` helper added
- **`providers.py`** — Anthropic + OpenAI-compatible provider abstraction
  (`AnthropicProvider`, `OpenAIProvider`, `make_provider`); accepts per-agent
  `tools` list
- **`tools.py`** — full tool set with per-agent subsets (`PLANNER_TOOLS`,
  `WORKER_TOOLS`, `REPORTER_TOOLS`); `locate_fastq_pairs` tool added;
  `start_job` description documents real-mode `r1`/`r2`/`bam_path` args
- **`orchestrator.py`** — original single flat agent loop; kept as legacy shim
- **`session.py`** — multi-turn web chat session; now uses Planner prompt +
  `PLANNER_TOOLS` + augmented dispatch (sub-agent wiring)
- **`prompts/`** — per-agent system prompts:
  `planner.py`, `wes.py`, `scrna.py`, `reporter.py`
- **`agents/`** — four-layer multi-agent architecture:
  - `planner.py` (L1): inspect → plan → dispatch_worker → generate_report
  - `wes_agent.py` (L2): QC → alignment → mutation_calling
  - `scrna_agent.py` (L2): annotation → clustering → DE → GSEA
  - `reporter.py` (L3): reads checkpoint, generates figures, writes report
- **`figures.py`** — six plot functions from checkpoint data (no AnnData needed):
  cell type composition, mock UMAP, cluster sizes, DE genes, GSEA enrichment,
  WES variant summary; saves PNGs under `report/figures/`
- **`agents/reporter.py`** — full HTML report template with sidebar TOC, per-step
  metric tables, figures gallery with relative-path `<img>` references;
  `_md_to_html_body`, `_build_html`, `_make_toc`
- **`server.py`** — stdlib HTTP server; GUI sessions now backed by Planner
- **`run_pipeline.py`** — CLI entrypoint; calls `agents.planner.run()`
- **`test_dispatch.py`** — no-API test driver; `--all`, `--sample`, `--limit` flags
- **Real-mode WES wiring** — `qc.py` and `alignment.py` raise clear `ValueError`
  when `r2` is missing; `locate_fastq_pairs` tool finds pairs from extracted
  fastq directories; `prompts/wes.py` documents the real-mode call sequence
- **Data symlinks** — `data/WES_OC_fasta`, `data/scRNA_AML`, `data/RefGenome`

---

## TODO

### 4. Minor / polish (3 sub-items remaining)

#### 4a. Remove or formalize `orchestrator.py`
`run_pipeline.py` now calls `agents/planner.py` directly. `orchestrator.py`
is unreferenced but still on disk. Either delete it or convert it to a one-line
shim that imports and calls `agents.planner.run` so old scripts don't break.

#### 4b. Fix scRNA step order in `test_dispatch.py`
`test_dispatch.py:51` runs `clustering` before `cell_annotation`, but the
canonical order defined in the README and `prompts/scrna.py` is
`cell_annotation → clustering → differential_expression → gsea`. Swap lines
51–52 in `run_scrna_branch`.

#### 4c. Session resume in GUI
`server.py` holds sessions in-memory only; they don't survive a restart.
The on-disk `state.json` does survive. Add a resume path so users can
reconnect to an in-progress run:
- `POST /api/sessions` with an existing `run_id` currently returns 409.
  Change it to reattach to the checkpoint instead (or add a separate
  `POST /api/sessions/{run_id}/resume` endpoint).
- Add a "Resume run" input to `static/index.html` that pre-fills `run_id`
  and skips the data-path/goal fields.
