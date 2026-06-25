"""Cell type annotation for an scRNA count matrix (.h5 / .h5ad).

Real mode does a lightweight marker-score annotation (no reference atlas
required): normalize -> score canonical ovarian-TME marker sets per cell ->
assign each cell to its highest-scoring type. Good enough to demonstrate
the pipeline; swap in a reference-based method (e.g. CellTypist, scVI label
transfer) for production-quality calls.
"""

from __future__ import annotations

from typing import Any

from . import seeded_random
from .detect import resolve_path

MARKER_SETS = {
    "Epithelial/Tumor": ["EPCAM", "KRT8", "KRT18", "PAX8", "WT1"],
    "Fibroblast/CAF": ["COL1A1", "COL1A2", "DCN", "PDGFRB"],
    "Endothelial": ["PECAM1", "VWF", "CDH5"],
    "T cell": ["CD3D", "CD3E", "CD2", "TRAC"],
    "NK cell": ["NKG7", "GNLY", "KLRD1"],
    "B cell": ["MS4A1", "CD79A", "CD19"],
    "Plasma cell": ["MZB1", "IGHG1", "JCHAIN"],
    "Myeloid/Macrophage": ["CD68", "CD14", "LYZ", "ITGAM"],
}


def _mock(sample_id: str, input_path: str, n_cells: int | None) -> dict[str, Any]:
    rng = seeded_random("annotation", sample_id, input_path)
    n_cells = n_cells or rng.randint(2000, 12000)
    weights = [rng.uniform(0.3, 1.0) for _ in MARKER_SETS]
    total_w = sum(weights)
    proportions = {ct: round(w / total_w, 3) for ct, w in zip(MARKER_SETS, weights)}
    counts = {ct: round(p * n_cells) for ct, p in proportions.items()}
    return {
        "sample_id": sample_id,
        "n_cells": n_cells,
        "marker_sets_used": MARKER_SETS,
        "cell_type_proportions": proportions,
        "cell_type_counts": counts,
    }


def _real(sample_id: str, input_path: str) -> dict[str, Any]:
    import scanpy as sc

    path = resolve_path(input_path)
    adata = sc.read_10x_h5(path) if path.suffix == ".h5" else sc.read_h5ad(path)
    adata.var_names_make_unique()

    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    scores = {}
    for cell_type, genes in MARKER_SETS.items():
        present = [g for g in genes if g in adata.var_names]
        if not present:
            continue
        sc.tl.score_genes(adata, present, score_name=f"score_{cell_type}")
        scores[cell_type] = adata.obs[f"score_{cell_type}"]

    import pandas as pd

    score_df = pd.DataFrame(scores)
    adata.obs["cell_type"] = score_df.idxmax(axis=1)
    counts = adata.obs["cell_type"].value_counts().to_dict()
    n_cells = int(adata.n_obs)
    proportions = {k: round(v / n_cells, 3) for k, v in counts.items()}
    return {
        "sample_id": sample_id,
        "n_cells": n_cells,
        "n_genes_used_for_scoring": int(score_df.notna().any(axis=0).sum()),
        "cell_type_counts": {k: int(v) for k, v in counts.items()},
        "cell_type_proportions": proportions,
    }


def run(*, sample_id: str, input_path: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, input_path, kwargs.get("n_cells"))
    if mode == "real":
        return _real(sample_id, input_path)
    raise ValueError(f"unknown mode: {mode}")
