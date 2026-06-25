#!/usr/bin/env python3
"""Terminal entrypoint for the LLM-orchestrated bioinformatics pipeline agent.

The agent inspects the data, presents a detailed analysis plan, waits for your
approval, then runs all pipeline steps and generates a Markdown + HTML report.

Examples:
    python run_pipeline.py                                      # Kang 2018 multimodal demo (default)
    python run_pipeline.py --data data/demo_multimodal         # same, explicit
    python run_pipeline.py --data data/demo/pbmc3k.h5ad --run-id pbmc-demo
    python run_pipeline.py --provider gemini --data /path/to/wes_fastqs --run-id wes1
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_pipeline.agents.planner import run
from agent_pipeline.providers import (
    ANTHROPIC_MODEL_DEFAULT,
    GEMINI_MODEL_DEFAULT,
    GROK_MODEL_DEFAULT,
    OPENAI_MODEL_DEFAULT,
)

_PROVIDER_DEFAULTS = {
    "anthropic": ANTHROPIC_MODEL_DEFAULT,
    "gemini":    GEMINI_MODEL_DEFAULT,
    "grok":      GROK_MODEL_DEFAULT,
    "openai":    OPENAI_MODEL_DEFAULT,
}

_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "grok":      "GROK_API_KEY",
    "openai":    "OPENAI_API_KEY",
}

DEFAULT_GOAL = (
    "Inspect the given data source, determine whether it is WES (exome) or scRNA data, "
    "and run the appropriate branch of the pipeline."
)

_DIVIDER = "─" * 64


def _prompt() -> str:
    """Read a line from the terminal, return empty string on Ctrl-C / EOF."""
    print(_DIVIDER)
    try:
        return input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data", default="data/demo_multimodal",
                        help="Path to the input data file or directory (default: data/demo_multimodal).")
    parser.add_argument("--run-id", default="cli-run",
                        help="Run identifier. Results go to result/<run-id>/.")
    parser.add_argument("--goal", default=DEFAULT_GOAL,
                        help="High-level instruction for the agent.")
    parser.add_argument("--provider", default="anthropic",
                        choices=list(_PROVIDER_DEFAULTS),
                        help="LLM provider (default: anthropic).")
    parser.add_argument("--api-key", default="",
                        help="API key. Falls back to the provider's env var if omitted.")
    parser.add_argument("--model", default=None,
                        help="Model name. Uses the provider default if omitted.")
    parser.add_argument("--base-url", default=None,
                        help="Custom base URL (for OpenAI-compatible endpoints).")
    parser.add_argument("--effort", default="high",
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args()

    # Resolve API key
    api_key = args.api_key or os.environ.get(_ENV_VARS.get(args.provider, ""), "")
    if not api_key:
        env_var = _ENV_VARS.get(args.provider, "<PROVIDER>_API_KEY")
        print(f"Error: no API key provided. Pass --api-key or set {env_var}.", file=sys.stderr)
        sys.exit(1)

    model = args.model or _PROVIDER_DEFAULTS[args.provider]

    print(_DIVIDER)
    print(f"  Bioinformatics Pipeline Agent — terminal mode")
    print(f"  run-id  : {args.run_id}")
    print(f"  data    : {args.data}")
    print(f"  provider: {args.provider}  model: {model}")
    print(f"  effort  : {args.effort}")
    print(_DIVIDER)
    print()

    final_text = run(
        run_id=args.run_id,
        goal=args.goal,
        data_path=args.data,
        provider_name=args.provider,
        api_key=api_key,
        model=model,
        base_url=args.base_url,
        effort=args.effort,
        auto_approve=True,
        max_iterations=args.max_iterations,
        input_fn=_prompt,
    )

    print()
    print(_DIVIDER)
    print(f"Run complete. Results in result/{args.run_id}/")
    print(f"  report   : result/{args.run_id}/report/report.html")
    print(f"  methods  : result/{args.run_id}/methods.md")
    print(f"  reproduce: result/{args.run_id}/reproduce.sh")
    print(_DIVIDER)


if __name__ == "__main__":
    main()
