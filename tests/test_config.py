from pathlib import Path

import pytest

from aorbot.config import config_from_dict, load_config

CONFIGS = sorted((Path(__file__).parent.parent / "configs").glob("*.toml"))


@pytest.mark.parametrize("path", CONFIGS, ids=[p.name for p in CONFIGS])
def test_shipped_configs_load(path):
    cfg = load_config(path)
    assert cfg.conditions.stage and cfg.conditions.car and cfg.conditions.weather


def test_shipped_configs_exist():
    assert len(CONFIGS) >= 2


def test_unknown_keys_are_rejected():
    with pytest.raises(ValueError, match="grp"):
        config_from_dict({"speed": {"grp": 5}})
    with pytest.raises(ValueError, match="section"):
        config_from_dict({"sped": {}})
