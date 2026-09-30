import json
import sys
import zipfile

import pytest

from zadbot import GAME_VERSION
from zadbot.game import find_game, read_version
from zadbot.modinstall import (
    ensure_installed, ensure_time_limit, install_mod, installed_params, mod_dependency, mod_dir, remove_slots,
    write_slot,
)
from zadbot.params import defaults, parse_params_js


def make_install(root, version, petra=True, zipped=True):
    exe = root / "binaries" / "system" / ("pyrogenesis.exe" if sys.platform == "win32" else "pyrogenesis")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    public = root / "binaries" / "data" / "mods" / "public"
    public.mkdir(parents=True)
    (public / "mod.json").write_text(json.dumps({"name": "0ad", "version": version, "dependencies": []}))
    if petra:
        if zipped:
            with zipfile.ZipFile(public / "public.zip", "w") as zf:
                zf.writestr("simulation/ai/petra/data.json", "{}")
        else:
            (public / "simulation/ai/petra").mkdir(parents=True)
            (public / "simulation/ai/petra/data.json").write_text("{}")
    return exe


def test_mod_pins_game_version():
    assert mod_dependency() == f"0ad={GAME_VERSION}"


@pytest.mark.parametrize("zipped", [True, False])
def test_find_game_from_folder(tmp_path, zipped):
    exe = make_install(tmp_path / "0ad", GAME_VERSION, zipped=zipped)
    for path in (tmp_path / "0ad", exe):
        game = find_game(str(path))
        assert game.version == GAME_VERSION
        assert game.has_petra()
        assert game.problems() == []


def test_other_version_is_refused(tmp_path):
    make_install(tmp_path / "0ad", "0.27.1")
    game = find_game(str(tmp_path / "0ad"))
    problems = game.problems()
    assert len(problems) == 1 and "0.27.1" in problems[0] and GAME_VERSION in problems[0]


def test_missing_petra_reported(tmp_path):
    make_install(tmp_path / "0ad", GAME_VERSION, petra=False)
    assert any("Petra" in p for p in find_game(str(tmp_path / "0ad")).problems())


def test_read_version_of_fake():
    game = find_game("fake")
    assert game.fake and read_version(game.data_dir) == GAME_VERSION


def test_install_mod_layout(tmp_path):
    dest = install_mod(tmp_path, None)
    assert dest == mod_dir(tmp_path)
    for rel in ["mod.json", "simulation/ai/zadbot/data.json", "simulation/ai/zadbot/_zadbot.js",
                "simulation/ai/zadbot/learned.js", "simulation/ai/zadbot/params.js", "maps/scripts/ZadbotReport.js",
                "simulation/data/settings/victory_conditions/zadbot_report.json"]:
        assert (dest / rel).is_file(), rel
    assert parse_params_js(installed_params(tmp_path)) == {}

    learned = dict(defaults(), aggressive=0.8)
    install_mod(tmp_path, learned, "learned")
    entries = parse_params_js(installed_params(tmp_path))
    assert entries["personality.aggressive"]["value"] == 0.8

    # updating the mod keeps what was learned
    ensure_installed(tmp_path)
    assert parse_params_js(installed_params(tmp_path))["personality.aggressive"]["value"] == 0.8


def test_slots(tmp_path):
    install_mod(tmp_path)
    name = write_slot(tmp_path, 3, dict(defaults(), workers=1.2))
    assert name == "zadbot_t3"
    d = mod_dir(tmp_path) / "simulation/ai/zadbot_t3"
    assert json.loads((d / "data.json").read_text())["constructor"] == "ZadBot"
    assert '"simulation/ai/zadbot_t3/params.js"' in (d / "_zadbot.js").read_text()
    assert parse_params_js((d / "params.js").read_text())["Economy.targetNumWorkers"]["value"] == 1.2
    assert remove_slots(tmp_path) == 1
    assert not d.exists()


def test_time_limit_condition(tmp_path):
    assert ensure_time_limit(tmp_path, 45) == "zadbot_limit_45"
    data = json.loads((mod_dir(tmp_path) / "simulation/data/settings/victory_conditions/zadbot_limit_45.json").read_text())
    assert data["Data"]["Scripts"] == ["scripts/ZadbotReport.js"]
