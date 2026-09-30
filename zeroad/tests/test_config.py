import pytest

from zadbot.config import BotConfig, config_from_dict, load_config


def test_defaults_valid():
    cfg = config_from_dict({})
    assert cfg == BotConfig()
    assert cfg.match.difficulty == 3
    assert "germ" in cfg.match.civs  # 0.28.0 added the Germans


def test_unknown_keys_rejected():
    with pytest.raises(ValueError, match="unknown key"):
        config_from_dict({"match": {"dificulty": 3}})
    with pytest.raises(ValueError, match="unknown section"):
        config_from_dict({"matches": {}})


@pytest.mark.parametrize("data", [
    {"match": {"difficulty": 9}},
    {"match": {"maps": []}},
    {"learn": {"elite": 8, "population": 8}},
    {"game": {"workers": 0}},
])
def test_invalid_rejected(data):
    with pytest.raises(ValueError):
        config_from_dict(data)


def test_shipped_config_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).parent.parent / "configs" / "default.toml")
    assert cfg.match.opponent == "petra"
