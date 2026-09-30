import json
import shutil

import pytest

from zadbot.copies import MARKER, is_current, is_inside, prepare_copies, worker_install
from zadbot.game import fake_install, parse_process_json


def fake_game_in(tmp_path):
    data = tmp_path / "src" / "binaries" / "data"
    shutil.copytree(fake_install().data_dir, data)
    return fake_install(data)


def test_prepare_copies_and_reuse(tmp_path):
    game = fake_game_in(tmp_path)
    logs = []
    workers = prepare_copies(game, 2, tmp_path / "copies", logs.append)
    assert len(workers) == 2 and "making 2 game copies" in logs[0]
    install, data = workers[1]
    assert data == tmp_path / "copies" / "worker1" / "binaries" / "data"
    assert install.command[-2:] == [f"--data-dir={data}", "-writableRoot"]
    assert install.logs_dir == tmp_path / "copies" / "worker1" / "binaries" / "logs"
    assert (data / "mods" / "public" / "mod.json").is_file()

    logs.clear()
    prepare_copies(game, 2, tmp_path / "copies", logs.append)
    assert logs == []  # up to date: nothing copied

    # the game changed (e.g. an update): copies are made again
    mod_json = game.data_dir / "mods" / "public" / "mod.json"
    mod_json.write_text(mod_json.read_text() + " ")
    assert not is_current(game, tmp_path / "copies" / "worker0")
    prepare_copies(game, 2, tmp_path / "copies", logs.append)
    assert "making 2 game copies" in logs[0]
    assert json.loads((tmp_path / "copies" / "worker0" / MARKER).read_text())["version"] == game.version


def test_not_enough_disk_space(tmp_path, monkeypatch):
    game = fake_game_in(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 10, 1))
    with pytest.raises(SystemExit, match="not enough disk space"):
        prepare_copies(game, 4, tmp_path / "copies", lambda m: None)


def test_real_game_copy_command(tmp_path):
    from dataclasses import replace

    game = replace(fake_install(), fake=False, command=[str(tmp_path / "0ad/binaries/system/pyrogenesis.exe")])
    install, data = worker_install(game, tmp_path / "w0")
    assert install.command == [str(tmp_path / "w0/binaries/system/pyrogenesis.exe"), "-writableRoot"]
    assert data == tmp_path / "w0/binaries/data"


def test_is_inside(tmp_path):
    assert is_inside(str(tmp_path / "a" / "b.exe"), tmp_path / "a")
    assert not is_inside(str(tmp_path / "ab" / "b.exe"), tmp_path / "a")
    assert not is_inside("", tmp_path)


def test_parse_process_json():
    one = '{"Id":28784,"Path":"C:\\\\Games\\\\0ad\\\\binaries\\\\system\\\\pyrogenesis.exe"}'
    assert parse_process_json(one) == [(28784, r"C:\Games\0ad\binaries\system\pyrogenesis.exe")]
    many = '[{"Id":1,"Path":"a"},{"Id":2,"Path":null}]'
    assert parse_process_json(many) == [(1, "a"), (2, "")]
    assert parse_process_json("") == []
