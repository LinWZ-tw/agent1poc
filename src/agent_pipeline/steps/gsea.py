"""Gene Set Enrichment Analysis on a ranked DE gene list.

Real mode runs gseapy.prerank against local .gmt files (no internet/Enrichr
dependency needed) -- confirmed present at data/RefGenome/*.gmt:
MSigDB_Hallmark_2020, KEGG_2021_Human, GO_Biological_Process_2023, Reactome_2022.
"""

from __future__ import annotations

from typing import Any

from . import seeded_random
from .detect import resolve_path

DEFAULT_GENE_SETS = "data/RefGenome/MSigDB_Hallmark_2020.gmt"

_HALLMARK_POOL = [
    "HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION",
    "HALLMARK_INFLAMMATORY_RESPONSE",
    "HALLMARK_TNFA_SIGNALING_VIA_NFKB",
    "HALLMARK_INTERFERON_GAMMA_RESPONSE",
    "HALLMARK_G2M_CHECKPOINT",
    "HALLMARK_OXIDATIVE_PHOSPHORYLATION",
    "HALLMARK_MYC_TARGETS_V1",
    "HALLMARK_DNA_REPAIR",
    "HALLMARK_ANGIOGENESIS",
    "HALLMARK_COMPLEMENT",
]


def _mock(sample_id: str, group: str | None) -> dict[str, Any]:
    rng = seeded_random("gsea", sample_id, str(group))
    n_hits = rng.randint(4, 7)
    pathways = rng.sample(_HALLMARK_POOL, n_hits)
    results = [
        {
            "pathway": p,
            "nes": round(rng.uniform(-2.8, 2.8), 2),
            "fdr_q_value": round(rng.uniform(1e-6, 0.05), 6),
            "n_genes_in_set": rng.randint(20, 200),
        }
        for p in pathways
    ]
    results.sort(key=lambda r: abs(r["nes"]), reverse=True)
    return {"sample_id": sample_id, "group": group, "gene_sets": "MSigDB_Hallmark_2020 (mock)", "enriched_pathways": results}


def _real(sample_id: str, ranked_genes: dict[str, float], gene_sets: str, group: str | None) -> dict[str, Any]:
    import gseapy as gp
    import pandas as pd

    gmt_path = resolve_path(gene_sets)
    if not gmt_path.exists():
        raise FileNotFoundError(f"gene set file not found: {gmt_path}")
    rnk = pd.Series(ranked_genes).sort_values(ascending=False)
    pre_res = gp.prerank(rnk=rnk, gene_sets=str(gmt_path), min_size=5, max_size=1000, permutation_num=100, seed=0)
    df = pre_res.res2d.sort_values("NES", key=abs, ascending=False).head(15)
    enriched = df.to_dict(orient="records")
    return {"sample_id": sample_id, "group": group, "gene_sets": str(gmt_path), "enriched_pathways": enriched}


def run(*, sample_id: str, mode: str = "mock", **kwargs: Any) -> dict[str, Any]:
    if mode == "mock":
        return _mock(sample_id, kwargs.get("group"))
    if mode == "real":
        ranked_genes = kwargs.get("ranked_genes")
        if not ranked_genes:
            raise ValueError("real-mode gsea requires `ranked_genes`: a {gene: score} mapping from the DE step")
        return _real(sample_id, ranked_genes, kwargs.get("gene_sets", DEFAULT_GENE_SETS), kwargs.get("group"))
    raise ValueError(f"unknown mode: {mode}")
