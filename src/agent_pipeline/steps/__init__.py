"""Pipeline step implementations.

Every step module exposes `run(*, mode="mock", **kwargs) -> dict`. `mode`
is either "mock" (fast, synthetic-but-plausible metrics seeded from real
input metadata) or "real" (shells out to / calls the actual tool). Real
mode is implemented but, per the agreed scope for this build, is not
exercised by the demo run.
"""

from __future__ import annotations

import hashlib
import random

# Pinned tool versions — update here when the toolchain is upgraded.
TOOL_VERSIONS: dict[str, str] = {
    "fastp":     "0.23.4",
    "bwa":       "0.7.17-r1198-dirty",
    "samtools":  "1.19.2",
    "gatk":      "4.4.0.0",
    "scanpy":    "1.9.6",
    "anndata":   "0.10.3",
    "harmonypy": "0.0.9",
    "leidenalg": "0.10.2",
    "gseapy":    "1.1.3",
    "python":    "3.11",
}


def seeded_random(*parts: str) -> random.Random:
    """Deterministic RNG so repeated mock runs on the same input agree."""
    key = "|".join(parts)
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def compute_seed(*parts: str) -> int:
    """Return the integer seed used by seeded_random(*parts) — for provenance logging."""
    key = "|".join(parts)
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)


CONDA_ENVS_ROOT = "/mnt/Storage5/weizhilin/miniconda3/envs"


def conda_run(env: str, *cmd: str) -> list[str]:
    """Build a `conda run -n <env> ...` command list for real-mode execution."""
    return ["conda", "run", "-n", env, *cmd]
