"""Find the installed 0 A.D., read its version, and know where it keeps user
data (mods, replays). Nothing here starts the game."""

from __future__ import annotations

import json
import os
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import GAME_VERSION

PETRA_DATA = "simulation/ai/petra/data.json"


@dataclass
class GameInstall:
    command: list[str]  # how to start the engine: [pyrogenesis] (or the fake game)
    data_dir: Path  # folder with mods/public/
    version: str
    fake: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def public_dir(self) -> Path:
        return self.data_dir / "mods" / "public"

    def has_petra(self) -> bool:
        if (self.public_dir / PETRA_DATA).is_file():
            return True
        archive = self.public_dir / "public.zip"
        if archive.is_file():
            with zipfile.ZipFile(archive) as zf:
                return PETRA_DATA in zf.namelist()
        return False

    def problems(self) -> list[str]:
        """Reasons the bot cannot run on this install (empty = fine)."""
        out = []
        if self.version != GAME_VERSION:
            out.append(
                f"0 A.D. {self.version} found, but this bot is built for {GAME_VERSION}: "
                f"the zadbot mod declares 0ad={GAME_VERSION} and the game will refuse it. "
                f"Install {GAME_VERSION} from https://play0ad.com/download/"
            )
        if not self.has_petra():
            out.append(f"Petra not found in {self.public_dir} (the zadbot AI is built on it)")
        return out


def read_version(data_dir: Path) -> str:
    """Version of the game's main mod ("public"), e.g. "0.28.0"."""
    with open(data_dir / "mods" / "public" / "mod.json", encoding="utf-8") as fh:
        return str(json.load(fh)["version"])


def _data_dir_for(binary: Path) -> Path | None:
    candidates = [
        binary.parent.parent / "data",  # binaries/system/ -> binaries/data (Windows, source), usr/bin -> usr/data (AppImage)
        binary.parent.parent / "Resources" / "data",  # macOS app bundle: Contents/MacOS -> Contents/Resources
        Path("/usr/share/games/0ad"),  # Debian/Ubuntu packages
        Path("/usr/share/0ad"),
    ]
    for c in candidates:
        if (c / "mods" / "public" / "mod.json").is_file():
            return c
    return None


def _default_binaries() -> list[Path]:
    exe = "pyrogenesis.exe" if sys.platform == "win32" else "pyrogenesis"
    roots: list[Path] = []
    if sys.platform == "win32":
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env)
            if base:
                roots += [Path(base) / "0 A.D. Empires Ascendant", Path(base) / "0 A.D."]
        return [r / "binaries" / "system" / exe for r in roots]
    if sys.platform == "darwin":
        return [Path("/Applications/0 A.D..app/Contents/MacOS/pyrogenesis")]
    return [
        Path("/usr/games/pyrogenesis"),
        Path("/usr/bin/pyrogenesis"),
        *sorted(Path("/opt").glob("0ad*/usr/bin/pyrogenesis")),
        *sorted(Path.home().glob("0ad*/usr/bin/pyrogenesis")),  # extracted AppImage
        *sorted(Path.home().glob("0ad*/binaries/system/pyrogenesis")),
    ]


def _binary_for(path: Path) -> Path:
    """Accept the binary itself or an install folder."""
    if path.is_file():
        return path
    exe = "pyrogenesis.exe" if sys.platform == "win32" else "pyrogenesis"
    for sub in (Path("binaries/system") / exe, Path("usr/bin") / exe, Path(exe)):
        if (path / sub).is_file():
            return path / sub
    raise FileNotFoundError(f"no {exe} in {path}")


def fake_install(data_dir: Path | None = None) -> GameInstall:
    """The stand-in game from zadbot.fakegame (see there)."""
    from . import fakegame

    data_dir = data_dir or fakegame.default_data_dir()
    return GameInstall(
        command=[sys.executable, "-m", "zadbot.fakegame", f"--data-dir={data_dir}"],
        data_dir=data_dir,
        version=read_version(data_dir),
        fake=True,
        notes=["fake game: a stand-in that plays pretend matches, for trying the pipeline"],
    )


def find_game(path: str = "") -> GameInstall:
    """`path`: pyrogenesis(.exe), an install folder, "fake", or "" to search."""
    if path == "fake":
        return fake_install()
    path = path or os.environ.get("ZADBOT_GAME", "")
    if path:
        binaries = [_binary_for(Path(path).expanduser())]
    else:
        binaries = [b for b in _default_binaries() if b.is_file()]
        if not binaries:
            raise FileNotFoundError(
                "0 A.D. not found. Pass --game <path to pyrogenesis or the install folder> "
                "or set [game] path in the config."
            )
    binary = binaries[0].resolve()
    data_dir = _data_dir_for(binary)
    if data_dir is None:
        raise FileNotFoundError(f"found {binary}, but no data/mods/public next to it")
    return GameInstall(command=[str(binary)], data_dir=data_dir, version=read_version(data_dir))


def _windows_documents() -> Path:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    # CSIDL_PERSONAL = 5, the folder the game uses (wutil_PersonalPath)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
        return Path(buf.value)
    return Path.home() / "Documents"


def default_user_data() -> Path:
    """Where 0 A.D. keeps mods and replays (source/ps/GameSetup/Paths.cpp)."""
    if sys.platform == "win32":  # pragma: no cover
        return _windows_documents() / "My Games" / "0ad"
    if sys.platform == "darwin":  # pragma: no cover
        return Path.home() / "Library" / "Application Support" / "0ad"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "0ad"


def user_data_dir(configured: str = "") -> Path:
    return Path(configured).expanduser() if configured else default_user_data()
