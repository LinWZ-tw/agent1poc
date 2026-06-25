"""DNA-exome alignment (bwa mem -> sorted, indexed BAM).

Real mode uses the pre-built bwa index at data/RefGenome/GRCh38 (confirmed
present: Homo_sapiens.GRCh38.dna.primary_assembly.fa + .bwt/.pac/.sa/.amb/.ann).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .. import REPO_ROOT
from . import TOOL_VERSIONS, conda_run, seeded_random
from .detect import resolve_path

DEFAULT_REFERENCE = "data/RefGenome/GRCh38/Homo_sapiens.GRCh38.dna.primary_assembly.fa"


def _mock(sample_id: str, input_path: str) -> dict[str, Any]:
    rng = seeded_random("alignment", sample_id, input_path)
    pct_mapped = round(rng.uniform(96.0, 99.5), 2)
    pct_properly_paired = round(rng.uniform(90.0, 98.0), 2)
    mean_target_coverage = round(rng.uniform(60.0, 140.0), 1)  # typical WES depth
    pct_target_bases_20x = round(rng.uniform(85.0, 98.0), 1)
    insert_size_mean = rng.randint(180, 260)
    return {
        "sample_id": sample_id,
        "reference": "GRCh38 (mock)",
        "pct_reads_mapped": pct_mapped,
        "pct_properly_paired": pct_properly_paired,
        "mean_target_coverage_x": mean_target_coverage,
        "pct_target_bases_at_20x": pct_target_bases_20x,
        "insert_size_mean_bp": insert_size_mean,
        "alignment_verdict": "pass" if pct_mapped > 95 and mean_target_coverage > 30 else "low_coverage",
        "_provenance": {
            "tool": "bwa mem + samtools sort",
            "version": f"bwa {TOOL_VERSIONS['bwa']}, samtools {TOOL_VERSIONS['samtools']}",
            "parameters": {"threads_bwa": 8, "threads_samtools": 4, "flags": "-M"},
            "random_seed": None,
            "reference": "GRCh38",
        },
    }


def _real(sample_id: str, r1: str, r2: str, output_dir: str, reference: str | None) -> dict[str, Any]:
    ref = resolve_path(reference or DEFAULT_REFERENCE)
    if not ref.exists():
        raise FileNotFoundError(f"reference genome fasta not found at {ref}")
    r1p, r2p = resolve_path(r1), resolve_path(r2)
    for fq in (r1p, r2p):
        if not fq.exists():
            raise FileNotFoundError(f"{fq} does not exist (expects trimmed fastq.gz from the QC step)")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bam = out / f"{sample_id}.sorted.bam"
    bwa_cmd = conda_run("wes", "bwa", "mem", "-t", "8", "-M", str(ref), str(r1p), str(r2p))
    sort_cmd = conda_run("wes", "samtools", "sort", "-@", "4", "-o", str(bam), "-")
    with subprocess.Popen(bwa_cmd, stdout=subprocess.PIPE, cwd=str(REPO_ROOT)) as bwa_proc:
        subprocess.run(sort_cmd, stdin=bwa_proc.stdout, check=True, cwd=str(REPO_ROOT))
    subprocess.run(conda_run("wes", "samtools", "index", str(bam)), check=True, cwd=str(REPO_ROOT))
    return {
        "sample_id": sample_id,
        "bam_path": str(bam),
        "reference": str(ref),
        "_provenance": {
            "tool": "bwa mem + samtools sort",
            "version": f"bwa {TOOL_VERSIONS['bwa']}, samtools {TOOL_VERSIONS['samtools']}",
            "parameters": {"threads_bwa": 8, "threads_samtools": 4, "flags": "-M"},
            "random_seed": None,
            "reference": str(ref),
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
                "real-mode alignment requires an explicit 'r1' argument (path to the trimmed R1 fastq.gz "
                "produced by the qc step). Call locate_fastq_pairs first if working from raw extracted fastqs."
            )
        if not r2:
            raise ValueError(
                "real-mode alignment requires an explicit 'r2' argument (path to the trimmed R2 fastq.gz "
                "produced by the qc step). Call locate_fastq_pairs first if working from raw extracted fastqs."
            )
        return _real(
            sample_id,
            r1,
            r2,
            kwargs.get("output_dir", "result/alignment"),
            kwargs.get("reference"),
        )
    raise ValueError(f"unknown mode: {mode}")
