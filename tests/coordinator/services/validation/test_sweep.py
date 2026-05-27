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
