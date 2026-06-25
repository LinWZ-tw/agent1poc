"""Generate reproducibility artifacts from a completed pipeline run.

Reads result/<run_id>/state.json and writes three files to result/<run_id>/:
  reproduce.sh  — exact shell commands (real-mode equivalents where mock was used)
  Snakefile     — Snakemake workflow for pipeline re-execution
  methods.md    — auto-generated Methods section, ready for inclusion in a paper
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state as _state
from .steps import TOOL_VERSIONS

_DEFAULT_REF   = "data/RefGenome/GRCh38/Homo_sapiens.GRCh38.dna.primary_assembly.fa"
_DEFAULT_DBSNP = "data/RefGenome/dbSNP_GCF_000001405.40.gz"
_DEFAULT_GMT   = "data/RefGenome/MSigDB_Hallmark_2020.gmt"

_WES_STEPS   = {"qc", "alignment", "mutation_calling"}
_SCRNA_STEPS = {"cell_annotation", "clustering", "differential_expression", "gsea"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _done_steps(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in checkpoint.get("steps", []) if s.get("status") == "done"]


# ── Shell script ──────────────────────────────────────────────────────────────

def _wes_shell_block(step: str, sid: str, inp: dict, prov: dict, mode: str) -> str:
    ref   = inp.get("reference", _DEFAULT_REF)
    dbsnp = inp.get("known_sites", _DEFAULT_DBSNP)
    r1    = inp.get("r1", f"<{sid}_R1.fastq.gz>")
    r2    = inp.get("r2", f"<{sid}_R2.fastq.gz>")
    C     = "conda run -n wes"

    if step == "qc":
        cmd = (
            f"{C} fastp \\\n"
            f"  -i {r1} -I {r2} \\\n"
            f"  -o result/qc/{sid}_R1.trimmed.fastq.gz \\\n"
            f"  -O result/qc/{sid}_R2.trimmed.fastq.gz \\\n"
            f"  --json result/qc/{sid}.fastp.json --thread 8"
        )
    elif step == "alignment":
        cmd = (
            f'{C} bash -c "\\\n'
            f"  bwa mem -t 8 -M {ref} \\\n"
            f"    result/qc/{sid}_R1.trimmed.fastq.gz \\\n"
            f"    result/qc/{sid}_R2.trimmed.fastq.gz \\\n"
            f'  | samtools sort -@ 4 -o result/alignment/{sid}.sorted.bam -"\n'
            f"{C} samtools index result/alignment/{sid}.sorted.bam"
        )
    elif step == "mutation_calling":
        cmd = (
            f"{C} gatk MarkDuplicates \\\n"
            f"  -I result/alignment/{sid}.sorted.bam \\\n"
            f"  -O result/mutation/{sid}.dedup.bam \\\n"
            f"  -M result/mutation/{sid}.dup_metrics.txt\n\n"
            f"{C} gatk BaseRecalibrator \\\n"
            f"  -I result/mutation/{sid}.dedup.bam \\\n"
            f"  -R {ref} \\\n"
            f"  --known-sites {dbsnp} \\\n"
            f"  -O result/mutation/{sid}.recal.table\n\n"
            f"{C} gatk ApplyBQSR \\\n"
            f"  -I result/mutation/{sid}.dedup.bam \\\n"
            f"  -R {ref} \\\n"
            f"  --bqsr-recal-file result/mutation/{sid}.recal.table \\\n"
            f"  -O result/mutation/{sid}.bqsr.bam\n\n"
            f"{C} gatk HaplotypeCaller \\\n"
            f"  -I result/mutation/{sid}.bqsr.bam \\\n"
            f"  -R {ref} \\\n"
            f"  -O result/mutation/{sid}.g.vcf.gz"
        )
    else:
        cmd = f"# (no template for step: {step})"

    header = [f"\n# step: {step}"]
    tool, ver = prov.get("tool", ""), prov.get("version", "")
    if tool and ver:
        header.append(f"# tool: {tool}  version: {ver}")
    if prov.get("random_seed") is not None:
        header.append(f"# random_seed: {prov['random_seed']}")
    if mode == "mock":
        header.append("# (mock run — commands below are the real-mode equivalents)")
    return "\n".join(header) + "\n" + cmd


def _scrna_shell_block(step: str, sid: str, inp: dict, prov: dict, mode: str) -> str:
    path       = inp.get("input_path", f"<{sid}.h5ad>")
    resolution = inp.get("resolution", 1.0)
    group_key  = inp.get("group_key", "leiden")

    if step == "cell_annotation":
        code = (
            "import scanpy as sc\n"
            f"adata = sc.read_h5ad('{path}')\n"
            "adata.var_names_make_unique()\n"
            "sc.pp.filter_cells(adata, min_genes=200)\n"
            "sc.pp.filter_genes(adata, min_cells=3)\n"
            "sc.pp.normalize_total(adata, target_sum=1e4)\n"
            "sc.pp.log1p(adata)\n"
            "# Score each cell against marker gene sets (see steps/annotation.py for MARKER_SETS):\n"
            "# sc.tl.score_genes(adata, MARKER_GENES, score_name='<CELL_TYPE>')\n"
            "# adata.obs['cell_type'] = scores_df.idxmax(axis=1)"
        )
    elif step == "clustering":
        code = (
            "import scanpy as sc\n"
            f"adata = sc.read_h5ad('{path}')\n"
            "adata.var_names_make_unique()\n"
            "sc.pp.filter_cells(adata, min_genes=200)\n"
            "sc.pp.filter_genes(adata, min_cells=3)\n"
            "sc.pp.normalize_total(adata, target_sum=1e4)\n"
            "sc.pp.log1p(adata)\n"
            "sc.pp.highly_variable_genes(adata, n_top_genes=2000)\n"
            "sc.pp.pca(adata, n_comps=30, use_highly_variable=True)\n"
            "# For multi-sample batch correction:\n"
            "# import scanpy.external as sce; sce.pp.harmony_integrate(adata, 'batch')\n"
            "sc.pp.neighbors(adata, use_rep='X_pca')\n"
            f"sc.tl.leiden(adata, resolution={resolution}, key_added='leiden')\n"
            "sc.tl.umap(adata)"
        )
    elif step == "differential_expression":
        code = (
            "import scanpy as sc, pandas as pd\n"
            f"adata = sc.read_h5ad('{path}')\n"
            f"sc.tl.rank_genes_groups(adata, groupby='{group_key}', method='wilcoxon')\n"
            "de_df = sc.get.rank_genes_groups_df(adata, group=None)\n"
            f"de_df.to_csv('result/diffexp/{sid}_de_results.csv')"
        )
    elif step == "gsea":
        code = (
            "import gseapy as gp, pandas as pd\n"
            f"de_df = pd.read_csv('result/diffexp/{sid}_de_results.csv', index_col=0)\n"
            "ranked = de_df.set_index('names')['logfoldchanges']\n"
            "pre_res = gp.prerank(\n"
            "    rnk=ranked,\n"
            f"    gene_sets='{_DEFAULT_GMT}',\n"
            "    min_size=5, max_size=1000, permutation_num=100, seed=0\n"
            ")\n"
            f"pre_res.res2d.to_csv('result/gsea/{sid}_gsea_results.csv')"
        )
    else:
        code = f"# (no template for step: {step})"

    header = [f"\n# step: {step}"]
    tool, ver = prov.get("tool", ""), prov.get("version", "")
    if tool and ver:
        header.append(f"# tool: {tool}  version: {ver}")
    seed = prov.get("random_seed")
    if seed is not None:
        header.append(f"# random_seed: {seed}")
    if mode == "mock":
        header.append("# (mock run — code below is the real-mode equivalent)")
    return "\n".join(header) + "\npython << 'PYEOF'\n" + code + "\nPYEOF"


def _build_shell_script(run_id: str, steps: list[dict]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        f"# Reproducibility script — run: {run_id}",
        f"# Generated: {_ts()}",
        "#",
        "# Pinned tool versions:",
    ]
    for tool, ver in TOOL_VERSIONS.items():
        lines.append(f"#   {tool}: {ver}")
    lines += [
        "#",
        "# Instructions:",
        "#   1. Replace <...> placeholders with your actual file paths.",
        "#   2. WES steps use conda env 'wes'. scRNA steps use base conda env (scanpy installed).",
        "#   3. Run from the repository root.",
        "",
        "set -euo pipefail",
    ]

    by_sample: dict[str, list[dict]] = {}
    for s in steps:
        sid = (s.get("inputs") or {}).get("sample_id", "unknown")
        by_sample.setdefault(sid, []).append(s)

    for sid, sample_steps in by_sample.items():
        lines.append(f"\n{'#' * 65}")
        lines.append(f"# Sample: {sid}")
        lines.append(f"{'#' * 65}")
        for s in sample_steps:
            step = s["step"]
            inp  = s.get("inputs") or {}
            prov = s.get("provenance") or {}
            mode = s.get("mode", "mock")
            if step in _WES_STEPS:
                lines.append(_wes_shell_block(step, sid, inp, prov, mode))
            elif step in _SCRNA_STEPS:
                lines.append(_scrna_shell_block(step, sid, inp, prov, mode))

    return "\n".join(lines) + "\n"


# ── Snakefile ─────────────────────────────────────────────────────────────────

def _build_snakefile(run_id: str, steps: list[dict]) -> str:
    wes_samples: list[str] = []
    scrna_samples: list[str] = []
    for s in steps:
        sid = (s.get("inputs") or {}).get("sample_id", "unknown")
        if s["step"] in _WES_STEPS and sid not in wes_samples:
            wes_samples.append(sid)
        elif s["step"] in _SCRNA_STEPS and sid not in scrna_samples:
            scrna_samples.append(sid)

    lines = [
        "# Snakefile — auto-generated reproducibility workflow",
        f"# Run ID: {run_id}",
        f"# Generated: {_ts()}",
        "#",
        "# Usage (from repo root):",
        "#   snakemake --cores 8 --use-conda",
        "#   Provide sample input paths in config.yaml (see samples section below).",
        "",
    ]

    if wes_samples:
        lines.append(f"WES_SAMPLES = {wes_samples!r}")
    if scrna_samples:
        lines.append(f"SCRNA_SAMPLES = {scrna_samples!r}")
    lines += [
        "",
        f"REF      = {_DEFAULT_REF!r}",
        f"DBSNP    = {_DEFAULT_DBSNP!r}",
        f"HALLMARK = {_DEFAULT_GMT!r}",
        "",
    ]

    # Build final targets
    targets = []
    if wes_samples:
        targets.append('        expand("result/mutation/{sample}.g.vcf.gz", sample=WES_SAMPLES),')
    if scrna_samples:
        targets.append('        expand("result/gsea/{sample}_gsea_results.csv", sample=SCRNA_SAMPLES),')

    lines += ["rule all:", "    input:"] + targets + [""]

    if wes_samples:
        lines += [
            "# ── WES rules ───────────────────────────────────────────────────────────────",
            "",
            "# Provide R1/R2 paths per sample in snakemake config:",
            "#   snakemake --config 'samples={\"SAMPLE\":{\"r1\":\"path/R1.fastq.gz\",\"r2\":\"path/R2.fastq.gz\"}}'",
            "",
            "rule qc:",
            "    input:",
            "        r1 = lambda w: config['samples'][w.sample]['r1'],",
            "        r2 = lambda w: config['samples'][w.sample]['r2']",
            "    output:",
            "        r1   = 'result/qc/{sample}_R1.trimmed.fastq.gz',",
            "        r2   = 'result/qc/{sample}_R2.trimmed.fastq.gz',",
            "        json = 'result/qc/{sample}.fastp.json'",
            "    conda: 'wes'",
            f"    # fastp v{TOOL_VERSIONS['fastp']}",
            "    shell:",
            "        'fastp -i {input.r1} -I {input.r2}'",
            "        ' -o {output.r1} -O {output.r2}'",
            "        ' --json {output.json} --thread 8'",
            "",
            "rule alignment:",
            "    input:",
            "        r1 = 'result/qc/{sample}_R1.trimmed.fastq.gz',",
            "        r2 = 'result/qc/{sample}_R2.trimmed.fastq.gz'",
            "    output:",
            "        bam = 'result/alignment/{sample}.sorted.bam',",
            "        bai = 'result/alignment/{sample}.sorted.bam.bai'",
            "    params: ref = REF",
            "    conda: 'wes'",
            f"    # bwa v{TOOL_VERSIONS['bwa']}, samtools v{TOOL_VERSIONS['samtools']}",
            "    shell:",
            "        'bwa mem -t 8 -M {params.ref} {input.r1} {input.r2}'",
            "        ' | samtools sort -@ 4 -o {output.bam} -'",
            "        ' && samtools index {output.bam}'",
            "",
            "rule mutation_calling:",
            "    input:",
            "        bam = 'result/alignment/{sample}.sorted.bam'",
            "    output:",
            "        vcf     = 'result/mutation/{sample}.g.vcf.gz',",
            "        dedup   = 'result/mutation/{sample}.dedup.bam',",
            "        metrics = 'result/mutation/{sample}.dup_metrics.txt'",
            "    params: ref = REF, dbsnp = DBSNP",
            "    conda: 'wes'",
            f"    # GATK4 v{TOOL_VERSIONS['gatk']}",
            "    shell:",
            "        'gatk MarkDuplicates -I {input.bam} -O {output.dedup} -M {output.metrics} && '",
            "        'gatk BaseRecalibrator -I {output.dedup} -R {params.ref}'",
            "        ' --known-sites {params.dbsnp}'",
            "        ' -O result/mutation/{wildcards.sample}.recal.table && '",
            "        'gatk ApplyBQSR -I {output.dedup} -R {params.ref}'",
            "        ' --bqsr-recal-file result/mutation/{wildcards.sample}.recal.table'",
            "        ' -O result/mutation/{wildcards.sample}.bqsr.bam && '",
            "        'gatk HaplotypeCaller'",
            "        ' -I result/mutation/{wildcards.sample}.bqsr.bam'",
            "        ' -R {params.ref} -O {output.vcf}'",
            "",
        ]

    if scrna_samples:
        lines += [
            "# ── scRNA rules ─────────────────────────────────────────────────────────────",
            "",
            "# Provide h5ad path per sample in snakemake config:",
            "#   snakemake --config 'samples={\"SAMPLE\":{\"h5ad\":\"path/sample.h5ad\"}}'",
            "",
            "rule cell_annotation:",
            "    input: h5ad = lambda w: config['samples'][w.sample]['h5ad']",
            "    output: h5ad = 'result/annotation/{sample}_annotated.h5ad'",
            f"    # scanpy v{TOOL_VERSIONS['scanpy']}",
            "    script: 'scripts/cell_annotation.py'",
            "",
            "rule clustering:",
            "    input:  h5ad = 'result/annotation/{sample}_annotated.h5ad'",
            "    output: h5ad = 'result/clustering/{sample}_clustered.h5ad'",
            f"    # scanpy v{TOOL_VERSIONS['scanpy']}, harmonypy v{TOOL_VERSIONS['harmonypy']}, leidenalg v{TOOL_VERSIONS['leidenalg']}",
            "    script: 'scripts/clustering.py'",
            "",
            "rule differential_expression:",
            "    input:  h5ad = 'result/clustering/{sample}_clustered.h5ad'",
            "    output: csv  = 'result/diffexp/{sample}_de_results.csv'",
            f"    # scanpy v{TOOL_VERSIONS['scanpy']}",
            "    script: 'scripts/differential_expression.py'",
            "",
            "rule gsea:",
            "    input:",
            "        de  = 'result/diffexp/{sample}_de_results.csv',",
            "        gmt = HALLMARK",
            "    output: csv = 'result/gsea/{sample}_gsea_results.csv'",
            f"    # gseapy v{TOOL_VERSIONS['gseapy']}",
            "    script: 'scripts/gsea.py'",
            "",
        ]

    return "\n".join(lines)


# ── Methods section ───────────────────────────────────────────────────────────

def _build_methods(run_id: str, steps: list[dict]) -> str:
    step_names  = {s["step"] for s in steps}
    has_wes     = bool(step_names & _WES_STEPS)
    has_scrna   = bool(step_names & _SCRNA_STEPS)

    by_sample: dict[str, list[dict]] = {}
    for s in steps:
        sid = (s.get("inputs") or {}).get("sample_id", "unknown")
        by_sample.setdefault(sid, []).append(s)
    n_samples = len(by_sample)

    resolution  = 1.0
    group_key   = "leiden"
    harmony_used = False
    for s in steps:
        if s["step"] == "clustering":
            resolution = (s.get("inputs") or {}).get("resolution", 1.0)
            if "harmony" in str((s.get("outputs") or {}).get("integration_method", "")).lower():
                harmony_used = True
        if s["step"] == "differential_expression":
            group_key = (s.get("inputs") or {}).get("group_key", "leiden")

    lines = [
        "## Methods",
        "",
        f"*Auto-generated from run `{run_id}` · {_ts()} · {n_samples} sample(s)*",
        "",
    ]

    if has_wes:
        lines += [
            "### Whole-Exome Sequencing Analysis",
            "",
            "#### Read Quality Control",
            f"Raw paired-end reads were processed with fastp (v{TOOL_VERSIONS['fastp']}) "
            f"using default adapter trimming, quality filtering (Phred ≥ 20), and a "
            f"minimum read length of 15 bp (8 threads).",
            "",
            "#### Read Alignment",
            f"Trimmed reads were aligned to the human reference genome (GRCh38 / hg38) "
            f"using BWA-MEM (v{TOOL_VERSIONS['bwa']}, flag `-M`) with 8 threads. "
            f"Alignments were coordinate-sorted and indexed with SAMtools (v{TOOL_VERSIONS['samtools']}).",
            "",
            "#### Variant Calling",
            f"Germline variant calling followed the GATK4 (v{TOOL_VERSIONS['gatk']}) "
            f"best-practices pipeline: optical duplicate marking (MarkDuplicates), "
            f"base quality score recalibration (BQSR) using dbSNP build 155 as known sites, "
            f"and variant calling with HaplotypeCaller in GVCF mode.",
            "",
        ]

    if has_scrna:
        harmony_sentence = (
            f"Batch correction across samples was performed with Harmony "
            f"(v{TOOL_VERSIONS['harmonypy']}) applied to the PCA embedding. "
            if harmony_used else
            "No batch correction was applied (single-sample data). "
        )
        lines += [
            "### Single-Cell RNA-seq Analysis",
            "",
            "#### Preprocessing and Cell Type Annotation",
            f"Data was analysed with Scanpy (v{TOOL_VERSIONS['scanpy']}) and "
            f"AnnData (v{TOOL_VERSIONS['anndata']}). "
            f"Cells with < 200 detected genes and genes detected in < 3 cells were removed. "
            f"Counts were library-size normalised to 10,000 per cell and log1p transformed. "
            f"Cell types were assigned by scoring each cell against canonical marker gene sets "
            f"(`sc.tl.score_genes`) and assigning each cell to the highest-scoring type.",
            "",
            "#### Dimensionality Reduction and Clustering",
            f"The 2,000 most highly variable genes were selected. "
            f"PCA (30 components) was computed on the highly variable gene subset. "
            f"{harmony_sentence}"
            f"A k-nearest-neighbour graph (k = 15) was built on the PCA/Harmony embedding, "
            f"followed by Leiden community detection (resolution = {resolution}) "
            f"implemented in leidenalg (v{TOOL_VERSIONS['leidenalg']}). "
            f"UMAP coordinates were computed for visualisation.",
            "",
            "#### Differential Expression",
            f"Differentially expressed genes between {group_key} groups were identified "
            f"using Scanpy's `rank_genes_groups` with the Wilcoxon rank-sum test. "
            f"Genes were ranked by normalised expression score; "
            f"those with Benjamini-Hochberg adjusted p-value < 0.05 were retained.",
            "",
            "#### Gene Set Enrichment Analysis",
            f"Pre-ranked GSEA was run with gseapy (v{TOOL_VERSIONS['gseapy']}) against "
            f"the MSigDB Hallmark gene set collection (50 sets). "
            f"Genes were ranked by log2 fold change from the DE step. "
            f"Enrichment was computed with 100 permutations; "
            f"gene sets with FDR q-value < 0.25 were considered significantly enriched.",
            "",
        ]

    lines += [
        "### Software and Reproducibility",
        "",
        "All analyses were performed with the bioinformatics pipeline agent "
        "(https://github.com/LinWZ-tw/agent1poc). "
        "Tool versions used in this run:",
        "",
        "| Tool | Version |",
        "|------|---------|",
    ]
    for tool, ver in TOOL_VERSIONS.items():
        lines.append(f"| {tool} | {ver} |")

    lines += [
        "",
        f"Exact commands are provided in `result/{run_id}/reproduce.sh`. "
        f"A Snakemake workflow for re-running the analysis is in `result/{run_id}/Snakefile`.",
        "",
        f"*Run ID: `{run_id}`*",
    ]

    return "\n".join(lines) + "\n"


# ── Public entry point ────────────────────────────────────────────────────────

def export_workflow(run_id: str) -> dict[str, str]:
    """Write reproduce.sh, Snakefile, and methods.md to result/<run_id>/."""
    checkpoint = _state.load_state(run_id)
    rdir       = _state.run_dir(run_id)
    done       = _done_steps(checkpoint)

    sh_path   = rdir / "reproduce.sh"
    snk_path  = rdir / "Snakefile"
    meth_path = rdir / "methods.md"

    sh_path.write_text(_build_shell_script(run_id, done))
    sh_path.chmod(0o755)
    snk_path.write_text(_build_snakefile(run_id, done))
    meth_path.write_text(_build_methods(run_id, done))

    return {
        "reproduce_sh":         str(sh_path),
        "snakefile":            str(snk_path),
        "methods_md":           str(meth_path),
        "n_steps_documented":   len(done),
    }
