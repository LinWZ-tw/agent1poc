"""Differential expression between groups (e.g. leiden clusters or cell types)."""

from __future__ import annotations

from typing import Any

from . import TOOL_VERSIONS, compute_seed, seeded_random
from .detect import resolve_path

_GENE_POOL = [
    "EPCAM", "KRT8", "KRT18", "PAX8", "WT1", "COL1A1", "COL1A2", "DCN",
    "PECAM1", "VWF", "CD3D", "CD3E", "NKG7", "GNLY", "MS4A1", "CD79A",
    "MZB1", "CD68", "CD14", "LYZ", "MKI67", "TOP2A", "VEGFA", "MMP9",
    "CXCL12", "HLA-DRA", "STAT1", "IFIT1", "S100A8", "S100A9",
]

_GENE_POOL_BG = [
    "FOXP3", "IL2RA", "CTLA4", "PDCD1", "LAG3", "HAVCR2", "TIGIT",
    "CCL2", "CCL5", "CXCL8", "CXCL10", "IL6", "TNF", "IFNG", "IL10",
    "TGFB1", "MYC", "BCL2", "TP53", "CDKN1A", "GAPDH", "ACTB", "B2M",
    "RPL13A", "RPS27A", "EEF1A1", "FTH1", "FTL", "HSPA1A", "HSP90AA1",
    "CD4", "CD8A", "CD8B", "FOXO1", "TCF7", "CCR7", "SELL", "CX3CR1",
    "GZMB", "PRF1", "FASLG", "IFITM1", "IFITM2", "IFITM3", "MX1", "OAS1",
    "ISG15", "ISG20", "RSAD2", "IFIT2", "IFIT3", "HERC5", "TRIM22",
    "APOE", "C1QA", "C1QB", "TYROBP", "FCER1G", "FCGR3A", "ITGAM",
    "PTPRC", "CD19", "CD27", "IGHM", "IGHA1", "IGHG1", "JCHAIN",
    "STMN1", "HMGB1", "HMGB2", "H2AFZ", "TUBB", "ACTG1", "VIM",
    "HSPA8", "HSP90AB1", "HSPB1", "PPIA", "HIF1A", "VEGFB", "EGFR",
    "CDH1", "CDH2", "SNAI1", "FN1", "ITGA5", "ITGB1", "COL4A1",
]


def _mock(sample_id: str, groups: list[str] | None) -> dict[str, Any]:
    _orig_groups = groups
    rng = seeded_random("diffexp", sample_id, str(groups))
    groups = groups or [f"leiden_{i}" for i in range(rng.randint(6, 10))]
    top_genes = {}
    for g in groups:
        genes = rng.sample(_GENE_POOL, 5)
        top_genes[g] = [
            {"gene": gene, "logfoldchange": round(rng.uniform(0.5, 4.0), 2), "pval_adj": round(rng.uniform(1e-50, 1e-3), 6)}
            for gene in genes
        ]

    # Build volcano data for the first group: background + significant up + significant down
    rng_v = seeded_random("volcano", sample_id, str(groups))
    sig_up = top_genes[groups[0]]
    sig_up_names = {r["gene"] for r in sig_up}
    bg_pool = [g for g in _GENE_POOL_BG if g not in sig_up_names]
    bg_sample = rng_v.sample(bg_pool, min(70, len(bg_pool)))
    volcano_data = [
        {"gene": g, "logfoldchange": round(rng_v.gauss(0, 0.45), 3),
         "pval_adj": round(rng_v.uniform(0.06, 1.0), 6)}
        for g in bg_sample
    ]
    volcano_data += sig_up  # significant up-regulated
    dn_pool = [g for g in _GENE_POOL + _GENE_POOL_BG if g not in sig_up_names and g not in bg_sample]
    for gene in rng_v.sample(dn_pool, min(8, len(dn_pool))):
        volcano_data.append({
            "gene": gene,
            "logfoldchange": round(-rng_v.uniform(1.2, 3.5), 3),
            "pval_adj": round(rng_v.uniform(1e-30, 0.04), 8),
        })

    return {
        "sample_id": sample_id,
        "groupby": "leiden_or_cell_type",
        "groups": groups,
        "top_de_genes": top_genes,
        "volcano_data": volcano_data,
        "volcano_group": groups[0],
        "_provenance": {
            "tool": "scanpy rank_genes_groups",
            "version": TOOL_VERSIONS["scanpy"],
            "parameters": {"method": "wilcoxon"},
            "random_seed": compute_seed("diffexp", sample_id, str(_orig_groups)),
        },
    }


def _real(sample_id: str, input_path: str, group_key: str, method: str) -> dict[str, Any]:
    import scanpy as sc

    path = resolve_path(input_path)
    adata = sc.read_10x_h5(path) if path.suffix == ".h5" else sc.read_h5ad(path)
    if group_key not in adata.obs.columns:
        raise ValueError(
            f"group_key '{group_key}' not found in adata.obs (have: {list(adata.obs.columns)}). "
            "Run clustering or annotation first and pass that column name."
        )
    sc.tl.rank_genes_groups(adata, groupby=group_key, method=method)
    groups = [str(g) for g in adata.obs[group_key].unique()]
    top_genes = {}
    for group in groups:
        df = sc.get.rank_genes_groups_df(adata, group=group).head(10)
        top_genes[group] = df.to_dict(orient="records")

    # Full DE table for the first group → volcano plot
    first_group = groups[0] if groups else None
    volcano_data: list[dict] = []
    volcano_group = first_group or ""
    if first_group:
        full_df = sc.get.rank_genes_groups_df(adata, group=first_group)
        for row in full_df.itertuples(index=False):
            volcano_data.append({
                "gene": str(row.names),
                "logfoldchange": float(row.logfoldchanges),
                "pval_adj": float(row.pvals_adj),
            })

    return {
        "sample_id": sample_id,
        "groupby": group_key,
        "method": method,
        "top_de_genes": top_genes,
        "volcano_data": volcano_data,
        "volcano_group": volcano_group,
        "_provenance": {
            "tool": "scanpy rank_genes_groups",
            "version": TOOL_VERSIONS["scanpy"],
            "parameters": {"method": method, "group_key": group_key},
            "random_seed": None,
        },
    }


def run(*, sample_id: str, input_path: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, kwargs.get("groups"))
    if mode == "real":
        return _real(sample_id, input_path, kwargs.get("group_key", "leiden"), kwargs.get("method", "wilcoxon"))
    raise ValueError(f"unknown mode: {mode}")
