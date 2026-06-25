"""Read QC for DNA-exome fastq (WES side of the pipeline).

Real mode wraps `fastp` from the `wes` conda env. It requires the input to
already be extracted fastq.gz files (this framework never auto-extracts a
multi-hundred-GB archive) -- if given a zip/dir instead, it reports exactly
what's missing rather than faking success.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .. import REPO_ROOT
from . import TOOL_VERSIONS, conda_run, seeded_random
from .detect import resolve_path


def _mock(sample_id: str, input_path: str) -> dict[str, Any]:
    rng = seeded_random("qc", sample_id, input_path)
    total_reads = rng.randint(40_000_000, 120_000_000)
    pct_pass = round(rng.uniform(92.0, 98.5), 1)
    q30 = round(rng.uniform(88.0, 96.0), 1)
    gc = round(rng.uniform(40.0, 48.0), 1)
    adapter_pct = round(rng.uniform(0.5, 4.0), 2)
    dup_pct = round(rng.uniform(5.0, 20.0), 1)
    return {
        "sample_id": sample_id,
        "total_reads": total_reads,
        "pct_reads_passing_filter": pct_pass,
        "q30_rate_pct": q30,
        "gc_content_pct": gc,
        "adapter_contamination_pct": adapter_pct,
        "duplication_rate_pct": dup_pct,
        "qc_verdict": "pass" if pct_pass > 90 and q30 > 85 else "marginal",
        "_provenance": {
            "tool": "fastp",
            "version": TOOL_VERSIONS["fastp"],
            "parameters": {"thread": 8, "quality": 20, "min_length": 15},
            "random_seed": None,
        },
    }


def _real(sample_id: str, r1: str, r2: str, output_dir: str) -> dict[str, Any]:
    r1p, r2p = resolve_path(r1), resolve_path(r2)
    for fq in (r1p, r2p):
        if not fq.exists():
            raise FileNotFoundError(
                f"{fq} does not exist. Real-mode QC requires extracted fastq.gz files; "
                "this framework does not auto-extract the source .zip archives."
            )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_report = out / f"{sample_id}.fastp.json"
    cmd = conda_run(
        "wes",
        "fastp",
        "-i", str(r1p), "-I", str(r2p),
        "-o", str(out / f"{sample_id}_R1.trimmed.fastq.gz"),
        "-O", str(out / f"{sample_id}_R2.trimmed.fastq.gz"),
        "--json", str(json_report),
        "--thread", "8",
    )
    subprocess.run(cmd, check=True, cwd=str(REPO_ROOT))
    return {
        "sample_id": sample_id,
        "fastp_json_report": str(json_report),
        "command": " ".join(cmd),
        "_provenance": {
            "tool": "fastp",
            "version": TOOL_VERSIONS["fastp"],
            "parameters": {"r1": r1, "r2": r2, "thread": 8},
            "random_seed": None,
        },
    }


def run(*, sample_id: str, input_path: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, input_path)
    if mode == "real":
        r1 = kwargs.get("r1")
        r2 = kwargs.get("r2")
        if not r1:
            raise ValueError(
                "real-mode qc requires an explicit 'r1' argument (path to the R1 fastq.gz). "
                "Call locate_fastq_pairs on the extracted fastq directory first to obtain r1/r2 paths."
            )
        if not r2:
            raise ValueError(
                "real-mode qc requires an explicit 'r2' argument (path to the R2 fastq.gz). "
                "Call locate_fastq_pairs on the extracted fastq directory first to obtain r1/r2 paths."
            )
        return _real(sample_id, r1, r2, kwargs.get("output_dir", "result/qc"))
    raise ValueError(f"unknown mode: {mode}")
