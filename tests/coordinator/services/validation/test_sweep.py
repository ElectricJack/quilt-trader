import pytest

from coordinator.services.validation.sweep import expand_grid, config_hash


def test_expand_grid_simple():
    space = {"vol_target": [0.10, 0.15], "lookbacks": [[7, 14], [7, 14, 28]]}
    configs = expand_grid(space)
    assert len(configs) == 4
    assert {"vol_target": 0.10, "lookbacks": [7, 14]} in configs
    assert {"vol_target": 0.15, "lookbacks": [7, 14, 28]} in configs


def test_expand_grid_empty_returns_single_empty_config():
    assert expand_grid({}) == [{}]


def test_config_hash_stable():
    a = {"x": 1, "y": [2, 3]}
    b = {"y": [2, 3], "x": 1}  # different key order
    assert config_hash(a) == config_hash(b)


def test_config_hash_changes_with_value():
    assert config_hash({"x": 1}) != config_hash({"x": 2})


from coordinator.services.validation.sweep import sample_random, sample_latin_hypercube


def test_random_sample_count_and_seed():
    space = {"vol_target": [0.05, 0.40], "lookback": [3, 90]}  # bounds, not discrete
    configs = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert len(configs) == 20
    for cfg in configs:
        assert 0.05 <= cfg["vol_target"] <= 0.40
        assert 3 <= cfg["lookback"] <= 90 and isinstance(cfg["lookback"], int)

    # Determinism
    configs2 = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert configs == configs2


def test_latin_hypercube_covers_range():
    import numpy as np
    space = {"vol_target": [0.05, 0.40]}
    configs = sample_latin_hypercube(space, n=10, seed=7, distributions={"vol_target": "uniform"})
    values = np.array([c["vol_target"] for c in configs])
    # Latin hypercube: each of 10 strata should be hit exactly once
    strata = ((values - 0.05) / (0.40 - 0.05) * 10).astype(int)
    assert len(set(strata)) == 10
