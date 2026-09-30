import os
import zipfile
from pathlib import Path

import pytest

from zadbot.config import BotConfig, GameConfig, LearnConfig, MatchConfig

JS_DIR = Path(__file__).parent / "js"
MOD_ROOT = Path(__file__).parent.parent / "zadbot" / "mod" / "zadbot"


@pytest.fixture
def fake_cfg(tmp_path) -> BotConfig:
    """Small, fast setup on the fake game."""
    return BotConfig(
        game=GameConfig(path="fake", user_data=str(tmp_path / "user"), workers=3, match_timeout=60),
        match=MatchConfig(civs=["athen", "rome"], time_limit=30),
        learn=LearnConfig(population=6, matches_per_candidate=2, elite=2, seed=11),
    )


@pytest.fixture(scope="session")
def petra_root(tmp_path_factory) -> Path | None:
    """simulation/ai/ from a real 0 A.D. install, or None (tests needing it skip).

    ZADBOT_PETRA_ROOT = a folder that contains simulation/ai/petra, or
    ZADBOT_GAME = pyrogenesis / the game folder (its public.zip is read)."""
    root = os.environ.get("ZADBOT_PETRA_ROOT")
    if root:
        return Path(root)
    from zadbot.game import find_game

    try:
        game = find_game(os.environ.get("ZADBOT_GAME", ""))
    except FileNotFoundError:
        return None
    if game.fake:
        return None
    if (game.public_dir / "simulation" / "ai" / "petra").is_dir():
        return game.public_dir
    archive = game.public_dir / "public.zip"
    if not archive.is_file():
        return None
    out = tmp_path_factory.mktemp("public")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(out, [n for n in zf.namelist() if n.startswith("simulation/ai/")])
    return out
