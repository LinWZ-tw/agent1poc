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
    return {
        "sample_id": sample_id,
        "groupby": "leiden_or_cell_type",
        "groups": groups,
        "top_de_genes": top_genes,
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
    top_genes = {}
    for group in adata.obs[group_key].unique():
        df = sc.get.rank_genes_groups_df(adata, group=str(group)).head(10)
        top_genes[str(group)] = df.to_dict(orient="records")
    return {
        "sample_id": sample_id,
        "groupby": group_key,
        "method": method,
        "top_de_genes": top_genes,
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
