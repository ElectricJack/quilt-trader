from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

import numpy as np


def expand_grid(parameter_space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cross-product expansion of a parameter space.

    Each key in parameter_space maps to a list of candidate values.
    Returns a list of fully-specified config dicts.
    """
    if not parameter_space:
        return [{}]
    keys = list(parameter_space.keys())
    value_lists = [parameter_space[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def config_hash(config: dict[str, Any]) -> str:
    """Stable hash of a config dict (key-order-independent)."""
    payload = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def sample_random(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Random sampling from a continuous-bounded parameter space.

    parameter_space: key -> [low, high]
    distributions: key -> "uniform" | "log_uniform" | "int_uniform"
    """
    rng = np.random.default_rng(seed)
    configs: list[dict[str, Any]] = []
    for _ in range(n):
        cfg: dict[str, Any] = {}
        for key, bounds in parameter_space.items():
            lo, hi = bounds[0], bounds[1]
            dist = distributions.get(key, "uniform")
            if dist == "uniform":
                cfg[key] = float(rng.uniform(lo, hi))
            elif dist == "log_uniform":
                cfg[key] = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            elif dist == "int_uniform":
                cfg[key] = int(rng.integers(lo, hi + 1))
            else:
                raise ValueError(f"Unknown distribution: {dist}")
        configs.append(cfg)
    return configs


def sample_latin_hypercube(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Latin-hypercube sampling: each parameter is binned into n strata, each
    stratum sampled exactly once."""
    rng = np.random.default_rng(seed)
    keys = list(parameter_space.keys())
    columns: dict[str, list[Any]] = {}
    for key in keys:
        lo, hi = parameter_space[key][0], parameter_space[key][1]
        dist = distributions.get(key, "uniform")
        edges = np.linspace(0.0, 1.0, n + 1)
        offsets = rng.uniform(0, 1.0 / n, n)
        unit = edges[:-1] + offsets
        rng.shuffle(unit)
        if dist == "uniform":
            vals = lo + unit * (hi - lo)
            columns[key] = [float(v) for v in vals]
        elif dist == "log_uniform":
            log_lo, log_hi = np.log(lo), np.log(hi)
            vals = np.exp(log_lo + unit * (log_hi - log_lo))
            columns[key] = [float(v) for v in vals]
        elif dist == "int_uniform":
            vals = lo + unit * (hi - lo + 1)
            columns[key] = [int(v) for v in vals]
        else:
            raise ValueError(f"Unknown distribution: {dist}")
    return [dict(zip(keys, row)) for row in zip(*[columns[k] for k in keys])]
