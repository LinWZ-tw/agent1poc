#!/usr/bin/env python3
"""CLI entrypoint for the Claude-orchestrated bioinformatics pipeline agent.

Examples:
    python run_pipeline.py --data data/WES_OC_fasta --run-id demo1
    python run_pipeline.py --data data/scRNA_AML --run-id demo2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_pipeline.agents.planner import run  # noqa: E402

DEFAULT_GOAL = (
    "Inspect the given data source, determine whether it is WES (exome) or scRNA data, "
    "and run the appropriate branch of the pipeline (QC/alignment/mutation-calling for WES, "
    "or cell-annotation/clustering/differential-expression/GSEA for scRNA), discovering any "
    "additional relevant existing data (e.g. processed scRNA matrices) along the way."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="High-level instruction for the agent.")
    parser.add_argument("--data", default="data/WES_OC_fasta", help="Primary data path (relative to repo root or absolute).")
    parser.add_argument("--run-id", default="demo", help="Run identifier; result/<run-id>/ holds state + logs.")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Auto-respond to confirmation requests (default: on).")
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args()

    print(f"=== run_id={args.run_id} data={args.data} effort={args.effort} ===\n")
    final_text = run(
        run_id=args.run_id,
        goal=args.goal,
        data_path=args.data,
        auto_approve=args.auto_approve,
        effort=args.effort,
        max_iterations=args.max_iterations,
    )
    print(f"\n=== done. state/log under result/{args.run_id}/ ===")
    if final_text:
        print("\nFinal summary:\n" + final_text)


if __name__ == "__main__":
    main()
