#!/usr/bin/env python3
"""Free, no-API-key test of the pipeline's step library + job queue + checkpointing.

This drives `agent_pipeline.tools.dispatch` directly with deterministic
Python branch logic (mirroring what the system prompt asks Claude to do)
instead of an actual Claude tool-use loop -- no ANTHROPIC_API_KEY needed
for the pipeline steps themselves.

The Reporter Agent (Layer 3) does need an LLM to synthesise the final report.
Pass --api-key (or set ANTHROPIC_API_KEY) to generate it; omit to skip it.

Examples:
    python test_dispatch.py --data data/demo/pbmc3k.h5ad --run-id demo
    python test_dispatch.py --data data/demo/pbmc3k.h5ad --run-id demo --api-key sk-ant-...
    python test_dispatch.py --data data/scRNA_AML                         # 1 sample (first found)
    python test_dispatch.py --data data/scRNA_AML --sample BMMNC-A_scRNA.h5  # 1 specific sample
    python test_dispatch.py --data data/scRNA_AML --all                   # every sample in the directory
    python test_dispatch.py --data data/scRNA_AML --all --limit 5         # first 5 samples
    python test_dispatch.py --data data/WES_OC_fasta
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_pipeline.tools import dispatch  # noqa: E402


def show(label: str, result: dict) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(result, indent=2, default=str)[:1500])


def run_job(run_id: str, step: str, args: dict) -> dict:
    started = dispatch(run_id, "start_job", {"step": step, "args": args}, auto_approve=True)
    job_id = started["job_id"]
    status = dispatch(run_id, "check_job_status", {"job_id": job_id}, auto_approve=True)
    while status["status"] == "running":
        time.sleep(0.2)
        status = dispatch(run_id, "check_job_status", {"job_id": job_id}, auto_approve=True)
    result = dispatch(run_id, "get_job_result", {"job_id": job_id}, auto_approve=True)
    show(f"{step}", result)
    if result["status"] != "ok":
        raise RuntimeError(f"step '{step}' failed: {result}")
    return result["result"]


def run_scrna_branch(run_id: str, sample_id: str, input_path: str, n_cells: int | None) -> None:
    run_job(run_id, "cell_annotation", {"sample_id": sample_id, "input_path": input_path, "mode": "mock", "n_cells": n_cells})
    clu = run_job(run_id, "clustering", {"sample_id": sample_id, "input_path": input_path, "mode": "mock", "n_cells": n_cells})
    de = run_job(
        run_id, "differential_expression",
        {"sample_id": sample_id, "input_path": input_path, "mode": "mock", "groups": list(clu["cluster_sizes"])},
    )
    run_job(run_id, "gsea", {"sample_id": sample_id, "mode": "mock", "group": next(iter(de["top_de_genes"]))})


def run_wes_branch(run_id: str, sample_id: str, input_path: str) -> None:
    run_job(run_id, "qc", {"sample_id": sample_id, "input_path": input_path, "mode": "mock"})
    run_job(run_id, "alignment", {"sample_id": sample_id, "input_path": input_path, "mode": "mock"})
    run_job(run_id, "mutation_calling", {"sample_id": sample_id, "input_path": input_path, "mode": "mock"})


def run_reporter(run_id: str, api_key: str, provider: str, model: str | None) -> None:
    print("\n=== Reporter Agent (Layer 3) ===")
    from agent_pipeline.agents.reporter import run as reporter_run
    result = reporter_run(
        run_id=run_id,
        provider_name=provider,
        api_key=api_key,
        model=model or None,
        auto_approve=True,
    )
    print(f"  report.md  : {result['report_md']}")
    print(f"  report.html: {result['report_html']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, help="Path to inspect (relative to repo root or absolute).")
    parser.add_argument("--run-id", default="test", help="result/<run-id>/ holds state.json for this run.")
    parser.add_argument("--sample", default=None, help="For a scrna_matrix_directory: filename of the one sample to use (default: first found).")
    parser.add_argument("--all", action="store_true", help="For a scrna_matrix_directory: run every sample found, not just the first.")
    parser.add_argument("--limit", type=int, default=None, help="With --all: cap how many samples to run (default: no cap).")
    # Reporter options
    parser.add_argument("--api-key", default="", help="LLM API key for the Reporter Agent. Falls back to ANTHROPIC_API_KEY env var.")
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"], help="LLM provider for the Reporter (default: anthropic).")
    parser.add_argument("--model", default="", help="Model name for the Reporter (default: provider default).")
    parser.add_argument("--no-report", action="store_true", help="Skip the Reporter Agent even if an API key is available.")
    args = parser.parse_args()

    profile = dispatch(args.run_id, "inspect_data_source", {"path": args.data}, auto_approve=True)
    show("inspect_data_source", profile)
    data_type = profile["data_type"]

    if data_type == "dna_exome_fastq_archive":
        sample_id = Path(profile["details"]["sample_entry_peeked"]).parent.name
        print(f"\n=> WES branch (sample_id={sample_id})")
        run_wes_branch(args.run_id, sample_id, args.data)

    elif data_type in ("scrna_count_matrix", "scrna_h5ad"):
        sample_id = Path(args.data).stem
        n_cells = profile["details"].get("n_cells") or profile["details"].get("n_obs_cells")
        print(f"\n=> scRNA branch (sample_id={sample_id}, n_cells={n_cells})")
        run_scrna_branch(args.run_id, sample_id, args.data, n_cells)

    elif data_type == "scrna_matrix_directory":
        # profile["details"]["sample_files"] is capped at 10 for display by
        # inspect_data_source -- get the full list via list_available_assets.
        assets = dispatch(args.run_id, "list_available_assets", {"root": args.data, "pattern": "*.h5", "limit": 1000}, auto_approve=True)
        all_samples = [f["path"] for f in assets["files"]]
        if not all_samples:
            print("\n=> no .h5 sample files found via list_available_assets")
            return

        if args.all:
            chosen_list = all_samples[: args.limit] if args.limit else all_samples
        elif args.sample:
            chosen_list = [args.sample]
        else:
            chosen_list = all_samples[:1]

        print(f"\n=> scRNA branch on {len(chosen_list)} of {len(all_samples)} sample(s) found in {args.data}")
        for i, chosen in enumerate(chosen_list, 1):
            sample_path = str(Path(args.data) / chosen)
            sub_profile = dispatch(args.run_id, "inspect_data_source", {"path": sample_path}, auto_approve=True)
            show(f"[{i}/{len(chosen_list)}] inspect_data_source({sample_path})", sub_profile)
            sample_id = Path(chosen).stem
            n_cells = sub_profile["details"]["n_cells"]
            print(f"\n=> running scRNA branch on {chosen} (sample_id={sample_id}, n_cells={n_cells})")
            run_scrna_branch(args.run_id, sample_id, sample_path, n_cells)

    else:
        print(f"\n=> data_type '{data_type}' has no test branch wired up here -- inspect manually.")
        return

    print(f"\n=== Pipeline steps done. Checkpoint: result/{args.run_id}/state.json ===")

    # --- Reporter Agent ---
    if args.no_report:
        print("\n(Reporter skipped via --no-report)")
        return

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print(
            "\n(Reporter skipped — no API key found. "
            "Pass --api-key sk-... or set ANTHROPIC_API_KEY to generate the report.)\n"
            f"  Report can be generated later by running the GUI (python server.py) "
            f"and resuming run '{args.run_id}'."
        )
        return

    run_reporter(args.run_id, api_key, args.provider, args.model or None)
    print(f"\n=== All done. Open result/{args.run_id}/report/report.html ===")


if __name__ == "__main__":
    main()
