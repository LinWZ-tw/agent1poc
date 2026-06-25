"""Data source inspection.

This is the step that makes the pipeline "adaptive": before committing to a
branch, the agent calls `inspect` on a path and gets back evidence about
what the data actually is, not what its directory name suggests.

Cheap by construction: zip archives are never extracted. We read the
central directory (`zipinfo`) and peek a few KB of one decompressed entry
via a piped `unzip -p | head`, which lets `head` close the pipe early
instead of decompressing the whole (multi-hundred-GB) entry. HDF5/AnnData
files are opened in read-only/backed mode so only headers are touched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .. import REPO_ROOT

_SCRNA_10X_RE = re.compile(r"_(R1|R2|R3|I1|I2)_001\.fastq", re.IGNORECASE)
_EXOME_RE = re.compile(r"\bexome\b", re.IGNORECASE)
_FASTQ_RE = re.compile(r"\.f(ast)?q(\.gz)?$", re.IGNORECASE)


def resolve_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p.resolve() if p.exists() else (REPO_ROOT / path)


def _peek_zip_entry(zip_path: Path, entry: str, n_bytes: int = 4000) -> str:
    """Read the first n_bytes of a decompressed zip entry without extracting it."""
    unzip_proc = subprocess.Popen(
        ["unzip", "-p", str(zip_path), entry],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        if entry.endswith(".gz"):
            gunzip_proc = subprocess.Popen(
                ["gunzip", "-c"],
                stdin=unzip_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            unzip_proc.stdout.close()
            head_proc = subprocess.run(
                ["head", "-c", str(n_bytes)], stdin=gunzip_proc.stdout, capture_output=True, timeout=30
            )
            gunzip_proc.stdout.close()
        else:
            head_proc = subprocess.run(
                ["head", "-c", str(n_bytes)], stdin=unzip_proc.stdout, capture_output=True, timeout=30
            )
        return head_proc.stdout.decode("utf-8", errors="replace")
    finally:
        unzip_proc.terminate()


def _classify_from_text(manifest_text: str, entry_names: list[str], fastq_peek: str) -> tuple[str, list[str]]:
    evidence: list[str] = []
    text_blob = manifest_text + "\n" + "\n".join(entry_names)

    is_10x_named = any(_SCRNA_10X_RE.search(n) for n in entry_names)
    is_exome_named = bool(_EXOME_RE.search(text_blob))

    if is_exome_named:
        evidence.append("manifest/entry paths contain 'EXOME'")
    if is_10x_named:
        evidence.append("entry names match 10x-style _R1_/_R2_/_I1_/_I2_ naming")

    # A real scRNA-seq R1 read is short and barcode/UMI-like (typically 26-28bp
    # for 10x v2/v3); a WES read is a long genomic fragment (100-150bp+).
    read_lengths = []
    lines = fastq_peek.splitlines()
    for i in range(1, len(lines), 4):
        if i < len(lines) and lines[i] and not lines[i].startswith("@"):
            read_lengths.append(len(lines[i]))
    if read_lengths:
        avg_len = sum(read_lengths) / len(read_lengths)
        evidence.append(f"peeked read length(s) ~{avg_len:.0f}bp from one fastq entry")
        if avg_len <= 30:
            evidence.append("read length consistent with 10x barcode+UMI read (R1), not genomic DNA")
        elif avg_len >= 75:
            evidence.append("read length consistent with genomic (exome/WGS) sequencing, not a barcode read")

    if is_exome_named and not is_10x_named:
        return "dna_exome_fastq_archive", evidence
    if is_10x_named and not is_exome_named:
        return "scrna_fastq_archive", evidence
    if read_lengths:
        avg_len = sum(read_lengths) / len(read_lengths)
        if avg_len >= 75:
            return "dna_exome_fastq_archive", evidence
        if avg_len <= 30:
            return "scrna_fastq_archive", evidence
    return "unknown_fastq_archive", evidence


def _inspect_zip_dir(path: Path) -> dict[str, Any]:
    zips = sorted(path.glob("*.zip"))
    manifests = sorted(path.glob("list_part_*")) + sorted(path.glob("*.md5*"))
    manifest_text = ""
    for m in manifests[:5]:
        try:
            manifest_text += m.read_text(errors="replace")[:2000] + "\n"
        except OSError:
            pass

    if not zips:
        return {
            "path": str(path),
            "data_type": "unknown",
            "evidence": ["directory has no .zip archives and no recognized manifest"],
        }

    zip_path = zips[0]
    try:
        listing = subprocess.run(
            ["zipinfo", "-1", str(zip_path)], capture_output=True, text=True, timeout=30
        )
        entry_names = listing.stdout.splitlines()
    except (subprocess.TimeoutExpired, OSError) as exc:
        entry_names = []
        manifest_text += f"\n(zipinfo failed: {exc})"

    fastq_entry = next((n for n in entry_names if _FASTQ_RE.search(n)), None)
    fastq_peek = _peek_zip_entry(zip_path, fastq_entry) if fastq_entry else ""

    data_type, evidence = _classify_from_text(manifest_text, entry_names, fastq_peek)
    total_size = sum(z.stat().st_size for z in zips)
    return {
        "path": str(path),
        "data_type": data_type,
        "evidence": evidence,
        "details": {
            "archive_count": len(zips),
            "total_compressed_bytes": total_size,
            "total_compressed_gb": round(total_size / 1e9, 1),
            "sample_entry_peeked": fastq_entry,
            "manifest_files": [m.name for m in manifests],
            "n_entries_in_first_archive": len(entry_names),
        },
    }


def _inspect_h5(path: Path) -> dict[str, Any]:
    import h5py

    with h5py.File(path, "r") as f:
        if "matrix" in f:  # CellRanger-style raw/filtered .h5
            grp = f["matrix"]
            shape = grp["shape"][:] if "shape" in grp else None
            n_genes, n_cells = (int(shape[0]), int(shape[1])) if shape is not None else (None, None)
            return {
                "path": str(path),
                "data_type": "scrna_count_matrix",
                "evidence": ["HDF5 file has a CellRanger-style 'matrix' group (already aligned/counted)"],
                "details": {"n_genes": n_genes, "n_cells": n_cells, "format": "10x_h5"},
            }
        return {
            "path": str(path),
            "data_type": "unknown_h5",
            "evidence": [f"HDF5 file present but no 'matrix' group; top-level keys={list(f.keys())}"],
        }


def _inspect_h5ad(path: Path) -> dict[str, Any]:
    import anndata as ad

    a = ad.read_h5ad(path, backed="r")
    return {
        "path": str(path),
        "data_type": "scrna_h5ad",
        "evidence": ["AnnData .h5ad file (already processed scRNA object)"],
        "details": {
            "n_obs_cells": a.n_obs,
            "n_vars_genes": a.n_vars,
            "obs_columns": list(a.obs.columns)[:20],
            "has_existing_annotation": any(
                c.lower() in ("cell_type", "celltype", "leiden", "cluster") for c in a.obs.columns
            ),
        },
    }


def _inspect_matrix_dir(path: Path) -> dict[str, Any]:
    """A directory of .h5/.h5ad files directly (no zip archive) -- e.g. a cohort
    of already-processed CellRanger matrices, one file per sample."""
    h5_files = sorted(path.glob("*.h5"))
    h5ad_files = sorted(path.glob("*.h5ad"))
    matrix_files = h5_files + h5ad_files
    if not matrix_files:
        return {
            "path": str(path),
            "data_type": "unknown",
            "evidence": ["directory has no .zip archives, no .h5/.h5ad matrices, and no recognized manifest"],
        }

    representative = matrix_files[0]
    peek = _inspect_h5(representative) if representative.suffix == ".h5" else _inspect_h5ad(representative)
    other_files = sorted(p.name for p in path.iterdir() if p.suffix not in (".h5", ".h5ad"))
    return {
        "path": str(path),
        "data_type": "scrna_matrix_directory",
        "evidence": [
            f"directory contains {len(matrix_files)} .h5/.h5ad count-matrix file(s), no fastq/zip archives",
            f"representative file '{representative.name}' classified as {peek['data_type']}",
        ],
        "details": {
            "n_matrix_files": len(matrix_files),
            "sample_files": [f.name for f in matrix_files[:10]],
            "representative_peek": peek.get("details"),
            "other_files": other_files[:10],
        },
    }


def inspect(path: str) -> dict[str, Any]:
    """Classify a data path as DNA-exome fastq, scRNA fastq, scRNA count matrix, etc."""
    p = resolve_path(path)
    if not p.exists():
        return {"path": str(p), "data_type": "missing", "evidence": [f"path does not exist: {p}"]}

    if p.is_dir():
        if any(p.glob("*.zip")):
            return _inspect_zip_dir(p)
        return _inspect_matrix_dir(p)
    if p.suffix == ".h5":
        return _inspect_h5(p)
    if p.suffix == ".h5ad":
        return _inspect_h5ad(p)
    if p.suffix == ".zip":
        return _inspect_zip_dir(p.parent)
    return {"path": str(p), "data_type": "unknown", "evidence": [f"unrecognized file type: {p.suffix}"]}
