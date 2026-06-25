"""Unsupervised clustering (Harmony integration when multi-sample + Leiden)."""

from __future__ import annotations

from typing import Any

from . import TOOL_VERSIONS, compute_seed, seeded_random
from .detect import resolve_path


def _mock(sample_id: str, input_path: str, n_cells: int | None) -> dict[str, Any]:
    rng = seeded_random("clustering", sample_id, input_path)
    n_cells = n_cells or rng.randint(2000, 12000)
    n_clusters = rng.randint(6, 12)
    weights = [rng.uniform(0.3, 1.0) for _ in range(n_clusters)]
    total_w = sum(weights)
    sizes = [round(w / total_w * n_cells) for w in weights]
    return {
        "sample_id": sample_id,
        "n_cells": n_cells,
        "n_clusters": n_clusters,
        "cluster_sizes": {f"leiden_{i}": s for i, s in enumerate(sizes)},
        "integration_method": "none (single sample, mock mode does not model batch integration)",
        "_provenance": {
            "tool": "scanpy + leidenalg",
            "version": f"scanpy {TOOL_VERSIONS['scanpy']}, leidenalg {TOOL_VERSIONS['leidenalg']}",
            "parameters": {"n_pcs": 30, "n_hvg": 2000, "resolution": 1.0},
            "random_seed": compute_seed("clustering", sample_id, input_path),
        },
    }


def _real(sample_id: str, input_path: str, batch_key: str | None, resolution: float) -> dict[str, Any]:
    import scanpy as sc

    path = resolve_path(input_path)
    adata = sc.read_10x_h5(path) if path.suffix == ".h5" else sc.read_h5ad(path)
    adata.var_names_make_unique()
    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=2000, subset=False)
    sc.pp.pca(adata, n_comps=30, use_highly_variable=True)

    integration_method = "none (single sample)"
    if batch_key and batch_key in adata.obs.columns and adata.obs[batch_key].nunique() > 1:
        import scanpy.external as sce

        sce.pp.harmony_integrate(adata, batch_key)
        sc.pp.neighbors(adata, use_rep="X_pca_harmony")
        integration_method = "harmony"
    else:
        sc.pp.neighbors(adata, use_rep="X_pca")

    sc.tl.leiden(adata, resolution=resolution, key_added="leiden")
    sizes = adata.obs["leiden"].value_counts().to_dict()
    tool_ver = f"scanpy {TOOL_VERSIONS['scanpy']}, leidenalg {TOOL_VERSIONS['leidenalg']}"
    if integration_method == "harmony":
        tool_ver += f", harmonypy {TOOL_VERSIONS['harmonypy']}"
    return {
        "sample_id": sample_id,
        "n_cells": int(adata.n_obs),
        "n_clusters": adata.obs["leiden"].nunique(),
        "cluster_sizes": {f"leiden_{k}": int(v) for k, v in sizes.items()},
        "integration_method": integration_method,
        "resolution": resolution,
        "_provenance": {
            "tool": "scanpy + harmonypy + leidenalg" if integration_method == "harmony" else "scanpy + leidenalg",
            "version": tool_ver,
            "parameters": {"n_pcs": 30, "n_hvg": 2000, "resolution": resolution, "batch_key": batch_key},
            "random_seed": None,
        },
    }


def run(*, sample_id: str, input_path: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, input_path, kwargs.get("n_cells"))
    if mode == "real":
        return _real(sample_id, input_path, kwargs.get("batch_key"), kwargs.get("resolution", 1.0))
    raise ValueError(f"unknown mode: {mode}")
