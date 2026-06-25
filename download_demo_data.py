#!/usr/bin/env python3
"""Download a small public scRNA-seq demo dataset for first-time users.

Downloads the classic PBMC 3k dataset — 2,700 peripheral blood mononuclear
cells × 32,738 genes, CellRanger output from 10x Genomics (public domain) —
via scanpy's built-in cache (~7 MB download), then saves it as an .h5ad file
that the pipeline's scRNA branch can process directly.

Usage:
    python download_demo_data.py

Output:
    data/demo/pbmc3k.h5ad   (~7 MB)

Then open the web GUI:
    python server.py          # http://127.0.0.1:8000
    # click "Use demo data" next to the Data path field, or enter:
    #   Data path: data/demo/pbmc3k.h5ad
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
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
    print("(public 10x Genomics dataset, no account required)\n")

    adata = sc.datasets.pbmc3k()
    adata.write_h5ad(out_path)

    print(f"\nSaved to: {out_path}")
    print(f"Shape: {adata.n_obs} cells x {adata.n_vars} genes")
    print("\nTo run the pipeline:")
    print("  python server.py        # open http://127.0.0.1:8000")
    print(f"  Data path: {out_path}")
    print("\n  OR via CLI (no API key needed):")
    print(f"  python test_dispatch.py --data {out_path} --run-id pbmc3k-demo")


if __name__ == "__main__":
    main()
