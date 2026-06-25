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


def seeded_random(*parts: str) -> random.Random:
    """Deterministic RNG so repeated mock runs on the same input agree."""
    key = "|".join(parts)
    seed = int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


CONDA_ENVS_ROOT = "/mnt/Storage5/weizhilin/miniconda3/envs"


def conda_run(env: str, *cmd: str) -> list[str]:
    """Build a `conda run -n <env> ...` command list for real-mode execution."""
    return ["conda", "run", "-n", env, *cmd]
