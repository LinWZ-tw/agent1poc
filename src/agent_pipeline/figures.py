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

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots as _plotly_subplots
    _PLOTLY = True
except ImportError:
    _PLOTLY = False


# ── helpers ──────────────────────────────────────────────────────────────────

def _rng(sample_id: str, tag: str) -> np.random.Generator:
    seed = int(hashlib.sha256(f"{tag}|{sample_id}".encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _save(fig: plt.Figure, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _div(plotly_fig) -> str:
    """Return an HTML div string for a Plotly figure (no bundled plotly.js)."""
    return plotly_fig.to_html(full_html=False, include_plotlyjs=False)


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


def plot_volcano(sample_id: str, diffexp: dict[str, Any], figures_dir: Path) -> str:
    """Volcano plot: -log10(padj) vs log2FC coloured by significance direction."""
    volcano_data = diffexp.get("volcano_data", [])
    if not volcano_data:
        return ""

    genes = [d["gene"] for d in volcano_data]
    lfc = np.array([d["logfoldchange"] for d in volcano_data], dtype=float)
    padj = np.clip([d["pval_adj"] for d in volcano_data], 1e-300, 1.0)
    neg_log_p = -np.log10(padj)

    sig_up = (padj < 0.05) & (lfc > 1)
    sig_dn = (padj < 0.05) & (lfc < -1)
    ns = ~sig_up & ~sig_dn

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(lfc[ns], neg_log_p[ns], c="#aaaaaa", s=12, alpha=0.45, linewidths=0)
    ax.scatter(lfc[sig_up], neg_log_p[sig_up], c="#e74c3c", s=18, alpha=0.85,
               linewidths=0, label=f"Up  (n={sig_up.sum()})")
    ax.scatter(lfc[sig_dn], neg_log_p[sig_dn], c="#3498db", s=18, alpha=0.85,
               linewidths=0, label=f"Down  (n={sig_dn.sum()})")

    ax.axhline(-np.log10(0.05), color="black", linewidth=0.7, linestyle="--", alpha=0.5)
    ax.axvline(1, color="black", linewidth=0.7, linestyle="--", alpha=0.5)
    ax.axvline(-1, color="black", linewidth=0.7, linestyle="--", alpha=0.5)

    # Label top hits by combined score
    sig_idx = np.where(sig_up | sig_dn)[0]
    if len(sig_idx):
        scores = neg_log_p[sig_idx] + np.abs(lfc[sig_idx])
        for i in sig_idx[np.argsort(scores)[-8:]]:
            ax.annotate(genes[i], (lfc[i], neg_log_p[i]),
                        fontsize=7, xytext=(3, 3), textcoords="offset points")

    group = diffexp.get("volcano_group", "")
    ax.set_xlabel("Log$_2$ Fold Change", fontsize=11)
    ax.set_ylabel("-log$_{10}$(adj. p-value)", fontsize=11)
    ax.set_title(f"Volcano Plot — {group}\n{sample_id}")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    return _save(fig, figures_dir / f"volcano_{sample_id}.png")


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


# ── Plotly (interactive) counterparts ────────────────────────────────────────

def _plotly_cell_type_composition(sample_id: str, annotation: dict[str, Any]):
    proportions = annotation.get("cell_type_proportions", {})
    if not proportions or not _PLOTLY:
        return None
    pairs = sorted(proportions.items(), key=lambda x: x[1], reverse=True)
    cell_types, values = zip(*pairs)
    fig = go.Figure(go.Bar(
        x=list(values), y=list(cell_types), orientation="h",
        text=[f"{v:.1%}" for v in values], textposition="outside",
        marker_color="rgba(99,110,250,0.8)",
    ))
    fig.update_layout(
        title=f"Cell Type Composition — {sample_id}",
        xaxis_title="Proportion of cells",
        yaxis=dict(autorange="reversed"),
        height=max(300, len(cell_types) * 35 + 80),
        margin=dict(l=10, r=70, t=50, b=40),
    )
    return fig


def _plotly_umap(sample_id: str, clustering: dict[str, Any]):
    cluster_sizes = clustering.get("cluster_sizes", {})
    if not cluster_sizes or not _PLOTLY:
        return None
    rng = _rng(sample_id, "umap")
    n_clusters = len(cluster_sizes)
    centers = rng.uniform(-8, 8, (n_clusters, 2))
    traces = []
    for i, (cluster, n) in enumerate(cluster_sizes.items()):
        n_pts = min(n, 400)
        pts = rng.normal(centers[i], scale=0.85, size=(n_pts, 2))
        traces.append(go.Scatter(
            x=pts[:, 0].tolist(), y=pts[:, 1].tolist(),
            mode="markers", marker=dict(size=4, opacity=0.6), name=cluster,
        ))
    fig = go.Figure(traces)
    fig.update_layout(
        title=f"UMAP — Leiden Clusters — {sample_id} (mock)",
        xaxis_title="UMAP 1 (mock)", yaxis_title="UMAP 2 (mock)",
        height=520,
    )
    return fig


def _plotly_cluster_sizes(sample_id: str, clustering: dict[str, Any]):
    sizes = clustering.get("cluster_sizes", {})
    if not sizes or not _PLOTLY:
        return None
    clusters = list(sizes.keys())
    counts = [sizes[c] for c in clusters]
    fig = go.Figure(go.Bar(
        x=clusters, y=counts, text=counts, textposition="outside",
        marker_color="rgba(99,110,250,0.75)",
    ))
    fig.update_layout(
        title=f"Cluster Sizes (Leiden) — {sample_id}",
        xaxis_title="Cluster", yaxis_title="Cell count",
        height=400,
    )
    return fig


def _plotly_de_genes(sample_id: str, diffexp: dict[str, Any]):
    top_de = diffexp.get("top_de_genes", {})
    if not top_de or not _PLOTLY:
        return None
    cluster = max(top_de, key=lambda c: len(top_de[c]))
    records = sorted(top_de[cluster], key=lambda g: abs(g["logfoldchange"]), reverse=True)[:10]
    if not records:
        return None
    genes = [g["gene"] for g in records]
    lfc = [g["logfoldchange"] for g in records]
    colors = ["#e74c3c" if v > 0 else "#3498db" for v in lfc]
    fig = go.Figure(go.Bar(
        x=lfc, y=genes, orientation="h",
        marker_color=colors,
        text=[f"{v:+.2f}" for v in lfc], textposition="outside",
    ))
    fig.update_layout(
        title=f"Top DE Genes — {cluster} — {sample_id}",
        xaxis_title="Log2 Fold Change",
        yaxis=dict(autorange="reversed"),
        height=max(300, len(genes) * 35 + 80),
        margin=dict(l=10, r=60, t=50, b=40),
    )
    return fig


def _plotly_volcano(sample_id: str, diffexp: dict[str, Any]):
    volcano_data = diffexp.get("volcano_data", [])
    if not volcano_data or not _PLOTLY:
        return None

    import math
    genes = [d["gene"] for d in volcano_data]
    lfc = [float(d["logfoldchange"]) for d in volcano_data]
    padj = [max(float(d["pval_adj"]), 1e-300) for d in volcano_data]
    neg_log_p = [-math.log10(p) for p in padj]

    categories = [
        "Up" if p < 0.05 and l > 1 else "Down" if p < 0.05 and l < -1 else "NS"
        for l, p in zip(lfc, padj)
    ]
    color_map = {"Up": "#e74c3c", "Down": "#3498db", "NS": "#aaaaaa"}
    size_map = {"Up": 7, "Down": 7, "NS": 4}

    traces = []
    for cat, color in color_map.items():
        idx = [i for i, c in enumerate(categories) if c == cat]
        if not idx:
            continue
        traces.append(go.Scatter(
            x=[lfc[i] for i in idx],
            y=[neg_log_p[i] for i in idx],
            mode="markers",
            marker=dict(color=color, size=size_map[cat], opacity=0.75),
            name=f"{cat} (n={len(idx)})",
            text=[genes[i] for i in idx],
            hovertemplate="%{text}<br>LFC: %{x:.3f}<br>-log10(padj): %{y:.2f}<extra></extra>",
        ))

    group = diffexp.get("volcano_group", "")
    fig = go.Figure(traces)
    fig.add_hline(y=-math.log10(0.05), line_dash="dash", line_color="gray", line_width=1)
    fig.add_vline(x=1, line_dash="dash", line_color="gray", line_width=1)
    fig.add_vline(x=-1, line_dash="dash", line_color="gray", line_width=1)
    fig.update_layout(
        title=f"Volcano Plot — {group} — {sample_id}",
        xaxis_title="Log2 Fold Change",
        yaxis_title="-log10(adj. p-value)",
        height=520,
    )
    return fig


def _plotly_gsea(sample_id: str, gsea_result: dict[str, Any]):
    pathways = gsea_result.get("enriched_pathways", [])
    if not pathways or not _PLOTLY:
        return None
    top = sorted(pathways, key=lambda p: abs(p["nes"]), reverse=True)[:10]
    top = sorted(top, key=lambda p: p["nes"])
    names = [p["pathway"].replace("HALLMARK_", "").replace("_", " ").title() for p in top]
    nes = [p["nes"] for p in top]
    colors = ["#e74c3c" if v > 0 else "#3498db" for v in nes]
    fig = go.Figure(go.Bar(
        x=nes, y=names, orientation="h",
        marker_color=colors,
        text=[f"{v:+.2f}" for v in nes], textposition="outside",
    ))
    fig.update_layout(
        title=f"GSEA — MSigDB Hallmark — {sample_id}",
        xaxis_title="Normalized Enrichment Score (NES)",
        yaxis=dict(autorange="reversed"),
        height=max(350, len(names) * 35 + 80),
        margin=dict(l=10, r=60, t=50, b=40),
    )
    return fig


def _plotly_wes_variants(sample_id: str, mutation: dict[str, Any]):
    if not _PLOTLY:
        return None
    n_snvs = mutation.get("n_snvs_raw", 0)
    n_indels = mutation.get("n_indels_raw", 0)
    n_pass = mutation.get("n_pass_variants", 0)
    variants = mutation.get("notable_oc_driver_variants", [])

    fig = _plotly_subplots(rows=1, cols=2, subplot_titles=["Variant Counts", "Driver VAFs"])
    cats = ["SNVs (raw)", "Indels (raw)", "PASS variants"]
    vals = [n_snvs, n_indels, n_pass]
    fig.add_trace(go.Bar(
        x=cats, y=vals, marker_color=["#3498db", "#9b59b6", "#2ecc71"],
        text=vals, textposition="outside", showlegend=False,
    ), row=1, col=1)
    if variants:
        csq_colors = {
            "missense_variant": "#e67e22", "frameshift_variant": "#e74c3c",
            "stop_gained": "#c0392b", "splice_donor_variant": "#8e44ad",
        }
        genes = [v["gene"] for v in variants]
        vafs = [v["vaf"] for v in variants]
        bar_colors = [csq_colors.get(v.get("consequence", ""), "#7f8c8d") for v in variants]
        fig.add_trace(go.Bar(
            x=vafs, y=genes, orientation="h",
            marker_color=bar_colors,
            text=[f"{v:.2f}" for v in vafs], textposition="outside",
            showlegend=False,
        ), row=1, col=2)
    fig.update_layout(title=f"WES Variant Summary — {sample_id}", height=480)
    return fig


# ── entry point ───────────────────────────────────────────────────────────────

_FIGURE_CAPTIONS = {
    "celltype_composition": "Cell Type Composition",
    "umap_clusters": "UMAP — Leiden Clusters (mock)",
    "cluster_sizes": "Cluster Sizes (Leiden)",
    "de_genes": "Top Differential Expression Genes",
    "volcano": "Volcano Plot",
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
) -> dict[str, list[dict[str, str]]]:
    """Generate all figures for every sample in the checkpoint.

    Returns a dict mapping sample_id → list of figure dicts, each with keys:
      - "png": absolute path to the saved PNG file
      - "html_div": Plotly interactive div HTML string (empty string if unavailable)
      - "caption": human-readable figure title
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

    def _entry(png_fn, plotly_fn, *args) -> dict[str, str] | None:
        png = png_fn(*args, figures_dir)
        if not png:
            return None
        plotly_fig = plotly_fn(*args) if _PLOTLY else None
        return {
            "png": png,
            "html_div": _div(plotly_fig) if plotly_fig is not None else "",
            "caption": _caption(png),
        }

    result: dict[str, list[dict[str, str]]] = {}
    for sid, steps in samples.items():
        entries: list[dict[str, str]] = []

        if "cell_annotation" in steps:
            e = _entry(plot_cell_type_composition, _plotly_cell_type_composition,
                       sid, steps["cell_annotation"])
            if e:
                entries.append(e)

        if "clustering" in steps:
            e = _entry(plot_mock_umap, _plotly_umap, sid, steps["clustering"])
            if e:
                entries.append(e)
            e = _entry(plot_cluster_sizes, _plotly_cluster_sizes, sid, steps["clustering"])
            if e:
                entries.append(e)

        if "differential_expression" in steps:
            e = _entry(plot_de_genes, _plotly_de_genes, sid, steps["differential_expression"])
            if e:
                entries.append(e)
            e = _entry(plot_volcano, _plotly_volcano, sid, steps["differential_expression"])
            if e:
                entries.append(e)

        if "gsea" in steps:
            e = _entry(plot_gsea, _plotly_gsea, sid, steps["gsea"])
            if e:
                entries.append(e)

        if "mutation_calling" in steps:
            e = _entry(plot_wes_variants, _plotly_wes_variants, sid, steps["mutation_calling"])
            if e:
                entries.append(e)

        if entries:
            result[sid] = entries

    return result
