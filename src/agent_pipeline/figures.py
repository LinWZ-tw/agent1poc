"""Figure generation for the pipeline report.

Produces synthetic-but-plausible plots from checkpoint step results (mock mode).
All figures are PNG files saved under result/<run_id>/report/figures/.
Real mode would pass an actual AnnData object to the UMAP/annotation functions;
mock mode generates Gaussian blobs seeded from the sample_id so the same run
always produces the same figures.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


# ── helpers ──────────────────────────────────────────────────────────────────

def _rng(sample_id: str, tag: str) -> np.random.Generator:
    seed = int(hashlib.sha256(f"{tag}|{sample_id}".encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _save(fig: plt.Figure, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(path)


# ── scRNA figures ─────────────────────────────────────────────────────────────

def plot_cell_type_composition(sample_id: str, annotation: dict[str, Any], figures_dir: Path) -> str:
    """Horizontal bar chart of cell type proportions."""
    proportions = annotation.get("cell_type_proportions", {})
    if not proportions:
        return ""

    pairs = sorted(proportions.items(), key=lambda x: x[1], reverse=True)
    cell_types, values = zip(*pairs)

    fig, ax = plt.subplots(figsize=(7, max(3.0, len(cell_types) * 0.45)))
    colors = plt.cm.Set2(np.linspace(0, 0.9, len(cell_types)))
    bars = ax.barh(list(cell_types), list(values), color=colors, edgecolor="white")
    ax.set_xlabel("Proportion of cells")
    ax.set_title(f"Cell Type Composition\n{sample_id}")
    ax.set_xlim(0, max(values) * 1.18)
    for bar, val in zip(bars, values):
        ax.text(val + 0.003, bar.get_y() + bar.get_height() / 2,
                f"{val:.1%}", va="center", fontsize=8)
    ax.invert_yaxis()
    fig.tight_layout()
    return _save(fig, figures_dir / f"celltype_composition_{sample_id}.png")


def plot_mock_umap(sample_id: str, clustering: dict[str, Any], figures_dir: Path) -> str:
    """Synthetic UMAP scatter plot: each Leiden cluster is a Gaussian blob."""
    cluster_sizes = clustering.get("cluster_sizes", {})
    if not cluster_sizes:
        return ""

    rng = _rng(sample_id, "umap")
    n_clusters = len(cluster_sizes)
    centers = rng.uniform(-8, 8, (n_clusters, 2))
    cmap = plt.cm.tab20

    fig, ax = plt.subplots(figsize=(6, 5))
    handles = []
    for i, (cluster, n) in enumerate(cluster_sizes.items()):
        n_pts = min(n, 400)
        pts = rng.normal(centers[i], scale=0.85, size=(n_pts, 2))
        color = cmap(i / max(n_clusters - 1, 1))
        ax.scatter(pts[:, 0], pts[:, 1], c=[color], s=5, alpha=0.6)
        handles.append(mpatches.Patch(color=color, label=cluster))

    ax.set_xlabel("UMAP 1 (mock)")
    ax.set_ylabel("UMAP 2 (mock)")
    ax.set_title(f"UMAP — Leiden Clusters\n{sample_id} (mock)")
    ax.legend(handles=handles, fontsize=7, loc="best",
              ncol=max(1, n_clusters // 7), framealpha=0.8)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    return _save(fig, figures_dir / f"umap_clusters_{sample_id}.png")


def plot_cluster_sizes(sample_id: str, clustering: dict[str, Any], figures_dir: Path) -> str:
    """Bar chart of cell counts per Leiden cluster."""
    sizes = clustering.get("cluster_sizes", {})
    if not sizes:
        return ""

    clusters = list(sizes.keys())
    counts = [sizes[c] for c in clusters]
    colors = plt.cm.tab20(np.linspace(0, 1, len(clusters)))

    fig, ax = plt.subplots(figsize=(max(6, len(clusters) * 0.65), 4))
    ax.bar(range(len(clusters)), counts, color=colors, edgecolor="white")
    ax.set_xticks(range(len(clusters)))
    ax.set_xticklabels(clusters, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Cell count")
    ax.set_title(f"Cluster Sizes (Leiden)\n{sample_id}")
    for i, c in enumerate(counts):
        ax.text(i, c + max(counts) * 0.01, str(c), ha="center", fontsize=8)
    fig.tight_layout()
    return _save(fig, figures_dir / f"cluster_sizes_{sample_id}.png")


def plot_de_genes(sample_id: str, diffexp: dict[str, Any], figures_dir: Path) -> str:
    """Horizontal bar chart of top DE genes for the largest cluster."""
    top_de = diffexp.get("top_de_genes", {})
    if not top_de:
        return ""

    cluster = max(top_de, key=lambda c: len(top_de[c]))
    gene_records = sorted(top_de[cluster], key=lambda g: abs(g["logfoldchange"]), reverse=True)[:10]
    if not gene_records:
        return ""

    genes = [g["gene"] for g in gene_records]
    lfc = [g["logfoldchange"] for g in gene_records]
    colors = ["#e74c3c" if v > 0 else "#3498db" for v in lfc]

    fig, ax = plt.subplots(figsize=(7, max(3.0, len(genes) * 0.42)))
    ax.barh(genes, lfc, color=colors, edgecolor="white")
    ax.axvline(0, color="black", linewidth=0.7, linestyle="--")
    ax.set_xlabel("Log2 Fold Change")
    ax.set_title(f"Top DE Genes — {cluster}\n{sample_id}")
    ax.invert_yaxis()
    up_patch = mpatches.Patch(color="#e74c3c", label="Up-regulated")
    dn_patch = mpatches.Patch(color="#3498db", label="Down-regulated")
    ax.legend(handles=[up_patch, dn_patch], fontsize=8)
    fig.tight_layout()
    return _save(fig, figures_dir / f"de_genes_{sample_id}.png")


def plot_gsea(sample_id: str, gsea_result: dict[str, Any], figures_dir: Path) -> str:
    """Horizontal bar chart of NES values for top enriched pathways."""
    pathways = gsea_result.get("enriched_pathways", [])
    if not pathways:
        return ""

    top = sorted(pathways, key=lambda p: abs(p["nes"]), reverse=True)[:10]
    top = sorted(top, key=lambda p: p["nes"])  # sort ascending for horizontal bar
    names = [p["pathway"].replace("HALLMARK_", "").replace("_", " ").title() for p in top]
    nes = [p["nes"] for p in top]
    colors = ["#e74c3c" if v > 0 else "#3498db" for v in nes]

    fig, ax = plt.subplots(figsize=(8, max(4, len(names) * 0.45)))
    ax.barh(names, nes, color=colors, edgecolor="white")
    ax.axvline(0, color="black", linewidth=0.7, linestyle="--")
    ax.set_xlabel("Normalized Enrichment Score (NES)")
    ax.set_title(f"GSEA — MSigDB Hallmark\n{sample_id}")
    up_patch = mpatches.Patch(color="#e74c3c", label="Enriched (NES > 0)")
    dn_patch = mpatches.Patch(color="#3498db", label="Depleted (NES < 0)")
    ax.legend(handles=[up_patch, dn_patch], fontsize=8, loc="lower right")
    fig.tight_layout()
    return _save(fig, figures_dir / f"gsea_{sample_id}.png")


# ── WES figures ───────────────────────────────────────────────────────────────

def plot_wes_variants(sample_id: str, mutation: dict[str, Any], figures_dir: Path) -> str:
    """Two-panel figure: variant counts bar chart + driver gene VAF chart."""
    n_snvs = mutation.get("n_snvs_raw", 0)
    n_indels = mutation.get("n_indels_raw", 0)
    n_pass = mutation.get("n_pass_variants", 0)
    variants = mutation.get("notable_oc_driver_variants", [])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, max(4, len(variants) * 0.55 + 2.5)))

    # Left panel — raw counts
    cats = ["SNVs (raw)", "Indels (raw)", "PASS variants"]
    vals = [n_snvs, n_indels, n_pass]
    cat_colors = ["#3498db", "#9b59b6", "#2ecc71"]
    ax1.bar(cats, vals, color=cat_colors, edgecolor="white", width=0.55)
    ax1.set_ylabel("Count")
    ax1.set_title(f"Variant Counts\n{sample_id}")
    for i, v in enumerate(vals):
        ax1.text(i, v + max(vals) * 0.015, f"{v:,}", ha="center", fontsize=9)
    ax1.set_ylim(0, max(vals) * 1.13)
    ax1.tick_params(axis="x", labelsize=9)

    # Right panel — driver gene VAFs
    if variants:
        genes = [v["gene"] for v in variants]
        vafs = [v["vaf"] for v in variants]
        consequences = [v.get("consequence", "unknown") for v in variants]
        csq_colors = {
            "missense_variant": "#e67e22",
            "frameshift_variant": "#e74c3c",
            "stop_gained": "#c0392b",
            "splice_donor_variant": "#8e44ad",
        }
        bar_colors = [csq_colors.get(c, "#7f8c8d") for c in consequences]
        bars = ax2.barh(genes, vafs, color=bar_colors, edgecolor="white")
        ax2.set_xlabel("Variant Allele Frequency (VAF)")
        ax2.set_xlim(0, 1.1)
        ax2.set_title(f"Driver Variant VAFs\n{sample_id}")
        ax2.invert_yaxis()
        for bar, vaf in zip(bars, vafs):
            ax2.text(vaf + 0.02, bar.get_y() + bar.get_height() / 2,
                     f"{vaf:.2f}", va="center", fontsize=9)
        seen_csq: dict[str, str] = {}
        for c, col in zip(consequences, bar_colors):
            seen_csq.setdefault(c, col)
        ax2.legend(
            handles=[mpatches.Patch(color=col, label=c.replace("_", " ")) for c, col in seen_csq.items()],
            fontsize=7, loc="lower right",
        )
    else:
        ax2.text(0.5, 0.5, "No notable driver\nvariants detected",
                 ha="center", va="center", transform=ax2.transAxes, fontsize=11)
        ax2.axis("off")

    fig.tight_layout()
    return _save(fig, figures_dir / f"wes_variants_{sample_id}.png")


# ── entry point ───────────────────────────────────────────────────────────────

_FIGURE_CAPTIONS = {
    "celltype_composition": "Cell Type Composition",
    "umap_clusters": "UMAP — Leiden Clusters (mock)",
    "cluster_sizes": "Cluster Sizes (Leiden)",
    "de_genes": "Top Differential Expression Genes",
    "gsea": "GSEA — Enriched Pathways",
    "wes_variants": "WES Variant Summary",
}


def _caption(png_path: str) -> str:
    stem = Path(png_path).stem  # e.g. "umap_clusters_BMMNC-A_scRNA"
    for prefix, caption in _FIGURE_CAPTIONS.items():
        if stem.startswith(prefix):
            return caption
    return stem


def generate_figures_for_run(
    run_id: str,
    checkpoint: dict[str, Any],
    report_dir: Path,
) -> dict[str, list[str]]:
    """Generate all figures for every sample in the checkpoint.

    Returns a dict mapping sample_id → list of PNG file paths.
    """
    figures_dir = report_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Group completed step outputs by sample_id
    samples: dict[str, dict[str, Any]] = {}
    for record in checkpoint.get("steps", []):
        if record.get("status") != "done":
            continue
        outputs = record.get("outputs") or {}
        sid = (record.get("inputs") or {}).get("sample_id") or outputs.get("sample_id")
        if not sid:
            continue
        samples.setdefault(sid, {})[record["step"]] = outputs

    result: dict[str, list[str]] = {}
    for sid, steps in samples.items():
        paths: list[str] = []

        if "cell_annotation" in steps:
            p = plot_cell_type_composition(sid, steps["cell_annotation"], figures_dir)
            if p:
                paths.append(p)

        if "clustering" in steps:
            p = plot_mock_umap(sid, steps["clustering"], figures_dir)
            if p:
                paths.append(p)
            p = plot_cluster_sizes(sid, steps["clustering"], figures_dir)
            if p:
                paths.append(p)

        if "differential_expression" in steps:
            p = plot_de_genes(sid, steps["differential_expression"], figures_dir)
            if p:
                paths.append(p)

        if "gsea" in steps:
            p = plot_gsea(sid, steps["gsea"], figures_dir)
            if p:
                paths.append(p)

        if "mutation_calling" in steps:
            p = plot_wes_variants(sid, steps["mutation_calling"], figures_dir)
            if p:
                paths.append(p)

        if paths:
            result[sid] = paths

    return result
