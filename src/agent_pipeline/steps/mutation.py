"""Germline/somatic variant calling on the aligned exome BAM (GATK4).

Real mode: MarkDuplicates -> BaseRecalibrator/ApplyBQSR (dbSNP known sites,
present at data/RefGenome/dbSNP_GCF_000001405.40.gz) -> HaplotypeCaller.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .. import REPO_ROOT
from . import TOOL_VERSIONS, conda_run, seeded_random
from .alignment import DEFAULT_REFERENCE
from .detect import resolve_path

DEFAULT_KNOWN_SITES = "data/RefGenome/dbSNP_GCF_000001405.40.gz"

# Genes recurrently mutated in high-grade serous ovarian carcinoma -- used to
# make mock variant calls read like a plausible OC cohort rather than noise.
_OC_GENE_POOL = ["TP53", "BRCA1", "BRCA2", "PIK3CA", "KRAS", "PTEN", "ARID1A", "NF1", "RB1", "CDK12"]


def _mock(sample_id: str, input_path: str) -> dict[str, Any]:
    rng = seeded_random("mutation", sample_id, input_path)
    n_snvs = rng.randint(15_000, 35_000)
    n_indels = rng.randint(1_500, 4_000)
    n_pass = round(n_snvs * rng.uniform(0.55, 0.75))
    n_genes_hit = rng.randint(2, 5)
    hit_genes = rng.sample(_OC_GENE_POOL, n_genes_hit)
    top_variants = [
        {
            "gene": g,
            "consequence": rng.choice(["missense_variant", "frameshift_variant", "stop_gained", "splice_donor_variant"]),
            "vaf": round(rng.uniform(0.15, 0.95), 2),
        }
        for g in hit_genes
    ]
    return {
        "sample_id": sample_id,
        "n_snvs_raw": n_snvs,
        "n_indels_raw": n_indels,
        "n_pass_variants": int(n_pass),
        "notable_oc_driver_variants": top_variants,
        "tp53_mutated": "TP53" in hit_genes,
        "_provenance": {
            "tool": "GATK4 HaplotypeCaller",
            "version": TOOL_VERSIONS["gatk"],
            "parameters": {
                "pipeline": "MarkDuplicates → BaseRecalibrator → ApplyBQSR → HaplotypeCaller",
                "known_sites": "dbSNP build 155 (GCF_000001405.40)",
            },
            "random_seed": None,
            "reference": "GRCh38 (mock)",
        },
    }


def _real(sample_id: str, bam_path: str, output_dir: str, reference: str | None, known_sites: str | None) -> dict[str, Any]:
    bam = resolve_path(bam_path)
    if not bam.exists():
        raise FileNotFoundError(f"{bam} does not exist (expects the sorted BAM from the alignment step)")
    ref = resolve_path(reference or DEFAULT_REFERENCE)
    sites = resolve_path(known_sites or DEFAULT_KNOWN_SITES)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    dedup_bam = out / f"{sample_id}.dedup.bam"
    metrics = out / f"{sample_id}.dup_metrics.txt"
    subprocess.run(
        conda_run("wes", "gatk", "MarkDuplicates", "-I", str(bam), "-O", str(dedup_bam), "-M", str(metrics)),
        check=True, cwd=str(REPO_ROOT),
    )

    recal_table = out / f"{sample_id}.recal.table"
    subprocess.run(
        conda_run(
            "wes", "gatk", "BaseRecalibrator",
            "-I", str(dedup_bam), "-R", str(ref), "--known-sites", str(sites), "-O", str(recal_table),
        ),
        check=True, cwd=str(REPO_ROOT),
    )
    bqsr_bam = out / f"{sample_id}.bqsr.bam"
    subprocess.run(
        conda_run(
            "wes", "gatk", "ApplyBQSR",
            "-I", str(dedup_bam), "-R", str(ref), "--bqsr-recal-file", str(recal_table), "-O", str(bqsr_bam),
        ),
        check=True, cwd=str(REPO_ROOT),
    )

    vcf = out / f"{sample_id}.g.vcf.gz"
    subprocess.run(
        conda_run("wes", "gatk", "HaplotypeCaller", "-I", str(bqsr_bam), "-R", str(ref), "-O", str(vcf)),
        check=True, cwd=str(REPO_ROOT),
    )
    return {
        "sample_id": sample_id,
        "vcf_path": str(vcf),
        "dedup_bam": str(dedup_bam),
        "_provenance": {
            "tool": "GATK4 HaplotypeCaller",
            "version": TOOL_VERSIONS["gatk"],
            "parameters": {
                "pipeline": "MarkDuplicates → BaseRecalibrator → ApplyBQSR → HaplotypeCaller",
                "known_sites": str(sites),
            },
            "random_seed": None,
            "reference": str(ref),
        },
    }


def run(*, sample_id: str, input_path: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, input_path)
    if mode == "real":
        bam_path = kwargs.get("bam_path")
        if not bam_path:
            raise ValueError(
                "real-mode mutation_calling requires an explicit 'bam_path' argument "
                "(path to the sorted BAM produced by the alignment step)."
            )
        return _real(
            sample_id,
            bam_path,
            kwargs.get("output_dir", "result/mutation"),
            kwargs.get("reference"),
            kwargs.get("known_sites"),
        )
    raise ValueError(f"unknown mode: {mode}")
