#!/usr/bin/env python3
"""Download demo datasets for first-time pipeline users.

Two demo modes:

  multimodal (default)
    Downloads the Kang et al. 2018 IFN-β stimulated PBMC dataset (GSE96583,
    ~100 MB) via the pertpy library, then splits it into four per-donor h5ad
    files (2 case = IFN-β stimulated, 2 control = unstimulated) and creates
    matching mock WES stub directories.  The result is a ready-to-run
    case-control scRNA + WES cohort at data/demo_multimodal/.

  scrna
    Downloads the classic PBMC 3k dataset (10x Genomics, ~7 MB) and saves it
    as data/demo/pbmc3k.h5ad.  Use this to quickly try the scRNA branch of
    the pipeline on a single sample (no pertpy needed).

Usage:
    python download_demo_data.py                  # multimodal demo (Kang 2018, default)
    python download_demo_data.py --demo scrna     # single-sample scRNA (PBMC 3k)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ─── scRNA demo (PBMC 3k) ────────────────────────────────────────────────────

def _download_scrna() -> None:
    try:
        import scanpy as sc
    except ImportError:
        print("scanpy is required: pip install scanpy")
        sys.exit(1)

    out_dir = Path("data/demo")
    out_path = out_dir / "pbmc3k.h5ad"

    if out_path.exists():
        print(f"Already downloaded: {out_path}")
        print(f"\n  Data path to use: {out_path}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    print("Downloading PBMC 3k dataset (~7 MB) from the scanpy data repository...")

    adata = sc.datasets.pbmc3k()
    adata.write_h5ad(out_path)

    print(f"\nSaved to: {out_path}")
    print(f"Shape: {adata.n_obs} cells x {adata.n_vars} genes")
    print("\nTo run the pipeline:")
    print("  python server.py        # open http://127.0.0.1:8000")
    print(f"  Data path: {out_path}")
    print("\n  OR via CLI:")
    print(f"  python run_pipeline.py --data {out_path} --run-id pbmc3k-demo")


# ─── Multimodal demo (Kang 2018 + WES stubs) ─────────────────────────────────

_STUDY = "Kang 2018 IFN-β stimulated PBMCs (case-control multimodal demo)"
_SOURCE = "GSE96583 — Kang et al., 2018, Nature Biotechnology"
_COMPARISON = "IFN-β stimulated PBMCs (case) vs. unstimulated control PBMCs"
_GROUP_COLUMN = "condition"

# Known column names in the Kang dataset (try in order)
_CONDITION_COLS = ("label", "stim", "condition", "group", "treatment")
_DONOR_COLS = ("replicate", "ind", "donor", "individual", "patient", "sample", "donor_id")


def _detect_column(obs_columns: list[str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in obs_columns:
            return c
    return None


def _download_multimodal() -> None:
    try:
        import pertpy as pt
    except ImportError:
        print("pertpy is required for the multimodal demo:")
        print("  pip install pertpy")
        sys.exit(1)

    try:
        import anndata  # noqa: F401
    except ImportError:
        print("anndata is required: pip install anndata")
        sys.exit(1)

    out_root = Path("data/demo_multimodal")
    manifest_path = out_root / "manifest.json"
    if manifest_path.exists():
        print(f"Multimodal demo already exists at {out_root}/")
        print(f"\n  Data path to use: {out_root}")
        return

    print("Downloading Kang 2018 IFN-β PBMC dataset (~100 MB) via pertpy...")
    print("  Source: GSE96583 (Kang et al., 2018, Nature Biotechnology)\n")

    adata = pt.data.kang_2018()
    obs_cols = list(adata.obs.columns)

    cond_col = _detect_column(obs_cols, _CONDITION_COLS)
    donor_col = _detect_column(obs_cols, _DONOR_COLS)

    if cond_col is None:
        print(f"Could not detect condition column in obs. Available: {obs_cols}")
        sys.exit(1)
    if donor_col is None:
        print(f"Could not detect donor column in obs. Available: {obs_cols}")
        sys.exit(1)

    print(f"  Condition column : '{cond_col}'  values: {sorted(adata.obs[cond_col].unique())}")
    print(f"  Donor column     : '{donor_col}' values: {sorted(adata.obs[donor_col].unique())[:8]}")

    conditions = sorted(adata.obs[cond_col].unique())
    # Map: first non-ctrl condition → "case", ctrl-like condition → "control"
    ctrl_labels = {"ctrl", "control", "unstimulated", "untreated", "vehicle", "unst"}
    ctrl_val = next((c for c in conditions if c.lower() in ctrl_labels), conditions[0])
    case_val = next((c for c in conditions if c != ctrl_val), None)

    if case_val is None:
        print(f"Expected at least two conditions; found only: {conditions}")
        sys.exit(1)

    print(f"\n  case  → '{case_val}'")
    print(f"  control → '{ctrl_val}'")

    # Pick the first 2 donors from each condition (for a lightweight demo)
    all_donors = sorted(adata.obs[donor_col].unique())
    donors = all_donors[:2]
    print(f"  Using 2 donors for demo: {donors}\n")

    scrna_dir = out_root / "scRNA"
    wes_dir = out_root / "WES"
    scrna_dir.mkdir(parents=True, exist_ok=True)
    wes_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    for cond_val, cond_label in [(case_val, "case"), (ctrl_val, "ctrl")]:
        for i, donor in enumerate(donors, start=1):
            sample_id = f"{cond_label}_donor{i}"
            mask = (adata.obs[cond_col] == cond_val) & (adata.obs[donor_col] == donor)
            subset = adata[mask].copy()

            # Add a normalised 'condition' column so the pipeline always finds it
            subset.obs["condition"] = cond_label

            scrna_path = scrna_dir / f"{sample_id}.h5ad"
            subset.write_h5ad(scrna_path)
            print(f"  Saved {scrna_path}  ({subset.n_obs} cells × {subset.n_vars} genes)")

            wes_path = wes_dir / sample_id
            wes_path.mkdir(exist_ok=True)
            (wes_path / "wes_stub.json").write_text(json.dumps({
                "type": "wes_mock_stub",
                "sample_id": sample_id,
                "condition": cond_label,
                "donor_id": str(donor),
                "note": (
                    "Mock WES stub — pipeline runs GATK HaplotypeCaller in "
                    "mock mode (no real FASTQ required for demo)."
                ),
            }, indent=2), encoding="utf-8")

            samples.append({
                "sample_id": sample_id,
                "condition": cond_label,
                "donor_id": str(donor),
                "scrna_path": str(scrna_path),
                "wes_path": str(wes_path),
                "n_cells": subset.n_obs,
                "n_genes": subset.n_vars,
            })

    manifest = {
        "study": _STUDY,
        "source": _SOURCE,
        "design": "case_control",
        "group_column": _GROUP_COLUMN,
        "comparison": _COMPARISON,
        "samples": samples,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nManifest written to {manifest_path}")
    print(f"\nMultimodal demo ready ({len(samples)} samples: "
          f"{sum(1 for s in samples if s['condition']=='case')} case, "
          f"{sum(1 for s in samples if s['condition']=='ctrl')} control)")
    print("\nTo run the pipeline:")
    print("  python server.py        # open http://127.0.0.1:8000")
    print(f"  Data path: {out_root}")
    print("\n  OR via CLI:")
    print(f"  python run_pipeline.py --data {out_root} --run-id kang-multimodal-demo")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--demo",
        default="multimodal",
        choices=["scrna", "multimodal"],
        help=(
            "Which demo dataset to download. "
            "'scrna' = PBMC 3k (default, ~7 MB, no extra deps). "
            "'multimodal' = Kang 2018 case-control scRNA + WES stubs (~100 MB, requires pertpy)."
        ),
    )
    args = parser.parse_args()

    if args.demo == "multimodal":
        _download_multimodal()
    else:
        _download_scrna()


if __name__ == "__main__":
    main()
