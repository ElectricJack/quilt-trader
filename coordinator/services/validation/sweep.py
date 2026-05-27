from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any


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
