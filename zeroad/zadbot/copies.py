"""Separate game copies, so several games can run at once on Windows.

On Windows a running game locks its files against other copies of the game
(see game.one_game_at_a_time). So each parallel worker gets its own copy of
the game folder and runs it with ``-writableRoot``. The copy then keeps its
mods, replays, config and cache in its own ``binaries/data`` and its logs in
``binaries/logs`` (source/ps/GameSetup/Paths.cpp). Workers never touch each
other's files or your normal 0 A.D. folders, so you can even play meanwhile.

Each copy is the whole ``binaries`` folder, about 4.2 GB for 0.28.0. Copies
are made once and reused while the game is unchanged.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .game import GameInstall

MARKER = "zadbot-copy.json"


def _source_binaries(game: GameInstall) -> Path:
    """The game's ``binaries`` folder (data_dir is binaries/data)."""
    return game.data_dir.parent


def _fingerprint(game: GameInstall) -> dict:
    """What identifies the source game: version and its biggest files."""
    public = game.data_dir / "mods" / "public"
    files = [public / "public.zip", public / "mod.json", game.data_dir / "mods" / "mod" / "mod.zip"]
    if not game.fake:
        files.append(Path(game.command[0]))
    return {
        "source": str(_source_binaries(game)),
        "version": game.version,
        "files": {f.name: [f.stat().st_size, int(f.stat().st_mtime)] for f in files if f.is_file()},
    }


def _tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def copy_size(game: GameInstall) -> int:
    """Bytes one copy needs."""
    src = _source_binaries(game)
    if game.fake:
        return _tree_size(game.data_dir)
    return _tree_size(src / "system") + _tree_size(src / "data")


def worker_install(game: GameInstall, dest: Path) -> tuple[GameInstall, Path]:
    """The GameInstall and user data folder of a prepared copy."""
    data = dest / "binaries" / "data"
    if game.fake:
        command = [*game.command[:-1], f"--data-dir={data}", "-writableRoot"]
    else:
        command = [str(dest / "binaries" / "system" / Path(game.command[0]).name), "-writableRoot"]
    copy = replace(game, command=command, data_dir=data, logs_dir=dest / "binaries" / "logs", notes=[])
    return copy, data


def is_current(game: GameInstall, dest: Path) -> bool:
    marker = dest / MARKER
    if not marker.is_file():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == _fingerprint(game)
    except (OSError, json.JSONDecodeError):
        return False


def prepare_copies(
    game: GameInstall, n: int, copies_dir: Path, log: Callable[[str], None],
) -> list[tuple[GameInstall, Path]]:
    """One copy per worker in ``copies_dir/worker<i>``; missing or outdated
    ones are (re)made. Returns (install, user data folder) per worker."""
    copies_dir.mkdir(parents=True, exist_ok=True)
    todo = [i for i in range(n) if not is_current(game, copies_dir / f"worker{i}")]
    if todo:
        size = copy_size(game)
        free = shutil.disk_usage(copies_dir).free
        need = size * len(todo)
        if need * 1.05 > free:
            raise SystemExit(
                f"not enough disk space for {len(todo)} game copies in {copies_dir}: they need "
                f"{need / 1e9:.1f} GB, {free / 1e9:.1f} GB is free. Use fewer --workers, or put the "
                f"copies on a bigger drive with [game] copies_dir or --copies-dir."
            )
        log(f"making {len(todo)} game copies for parallel games in {copies_dir} "
            f"({size / 1e9:.1f} GB each, only needed once)")
    src = _source_binaries(game)
    for i in todo:
        dest = copies_dir / f"worker{i}"
        if dest.exists():
            shutil.rmtree(dest)
        log(f"  copying the game to {dest} ...")
        if game.fake:
            shutil.copytree(game.data_dir, dest / "binaries" / "data")
        else:
            shutil.copytree(src / "system", dest / "binaries" / "system")
            shutil.copytree(src / "data", dest / "binaries" / "data")
        (dest / MARKER).write_text(json.dumps(_fingerprint(game)), encoding="utf-8")
    return [worker_install(game, copies_dir / f"worker{i}") for i in range(n)]


def is_inside(path: str, folder: Path) -> bool:
    """Case-insensitive on Windows, like the file system."""
    if not path:
        return False
    p, f = str(Path(path).resolve()), str(folder.resolve())
    if sys.platform == "win32":
        p, f = p.lower(), f.lower()
    return p.startswith(f.rstrip("\\/") + ("\\" if sys.platform == "win32" else "/"))
