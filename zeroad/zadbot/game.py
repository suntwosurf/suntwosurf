"""Find the installed 0 A.D., read its version, and know where it keeps user
data (mods, replays). Nothing here starts the game."""

from __future__ import annotations

import csv
import json
import os
import subprocess
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
    # where the game writes mainlog/interestinglog; None = <user data>/logs (fake game)
    logs_dir: Path | None = None

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
    """Version of the game's main mod ("public"), e.g. "0.28.0". Like the
    engine (Mod.cpp), fall back to the mod.json inside public.zip."""
    public = data_dir / "mods" / "public"
    if (public / "mod.json").is_file():
        text = (public / "mod.json").read_text(encoding="utf-8")
    else:
        with zipfile.ZipFile(public / "public.zip") as zf:
            text = zf.read("mod.json").decode("utf-8")
    return str(json.loads(text)["version"])


def _has_public(data_dir: Path) -> bool:
    public = data_dir / "mods" / "public"
    return (public / "mod.json").is_file() or (public / "public.zip").is_file()


def _exe_name() -> str:
    return "pyrogenesis.exe" if sys.platform == "win32" else "pyrogenesis"


def _exe_in(folder: Path, max_depth: int = 4) -> Path | None:
    """pyrogenesis in an install folder: the usual places first, then a
    search a few folders deep (for layouts other than the installer's)."""
    exe = _exe_name()
    for sub in (Path("binaries/system") / exe, Path("usr/bin") / exe, Path(exe)):
        if (folder / sub).is_file():
            return folder / sub
    if folder.is_dir():
        for found in sorted(folder.rglob(exe)):
            if len(found.relative_to(folder).parts) <= max_depth:
                return found
    return None


def _data_dir_for(binary: Path) -> Path | None:
    candidates = [
        binary.parent.parent / "data",  # binaries/system/ -> binaries/data (Windows, source), usr/bin -> usr/data (AppImage)
        binary.parent.parent / "Resources" / "data",  # macOS app bundle: Contents/MacOS -> Contents/Resources
        Path("/usr/share/games/0ad"),  # Debian/Ubuntu packages
        Path("/usr/share/0ad"),
    ]
    for c in candidates:
        if _has_public(c):
            return c
    return None


def _default_binaries() -> list[Path]:
    """Where to look for the game; on Windows an existing install folder is
    searched (see _exe_in)."""
    exe = _exe_name()
    roots: list[Path] = []
    if sys.platform == "win32":
        roots += _registry_install_dirs()
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env)
            if base:
                roots += [Path(base) / "0 A.D. Empires Ascendant", Path(base) / "0 A.D."]
        return [_exe_in(r) or r / "binaries" / "system" / exe for r in roots]
    if sys.platform == "darwin":
        return [Path("/Applications/0 A.D..app/Contents/MacOS/pyrogenesis")]
    return [
        Path("/usr/games/pyrogenesis"),
        Path("/usr/bin/pyrogenesis"),
        *sorted(Path("/opt").glob("0ad*/usr/bin/pyrogenesis")),
        *sorted(Path.home().glob("0ad*/usr/bin/pyrogenesis")),  # extracted AppImage
        *sorted(Path.home().glob("0ad*/binaries/system/pyrogenesis")),
    ]


def _registry_install_dirs() -> list[Path]:  # pragma: no cover - Windows only
    """Where the 0 A.D. installer says it installed the game. It writes
    "Software\\0 A.D." (default value) and the uninstall entry's
    InstallLocation, for the current user or all users
    (source/tools/dist/0ad.nsi)."""
    import winreg

    keys = [
        (r"Software\0 A.D.", ""),
        (r"Software\Microsoft\Windows\CurrentVersion\Uninstall\0 A.D.", "InstallLocation"),
        (r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\0 A.D.", "InstallLocation"),
    ]
    out: list[Path] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key, value in keys:
            try:
                with winreg.OpenKey(hive, key) as k:
                    text, _ = winreg.QueryValueEx(k, value)
            except OSError:
                continue
            text = str(text).strip().strip('"')
            if text and Path(text) not in out:
                out.append(Path(text))
    return out


def _binary_for(path: Path) -> Path:
    """Accept the binary itself or an install folder."""
    if path.is_file():
        return path
    found = _exe_in(path)
    if found is None:
        what = "does not exist" if not path.exists() else "has no " + _exe_name()
        raise FileNotFoundError(f"{path} {what}")
    return found


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
        searched = _default_binaries()
        binaries = [b for b in searched if b.is_file()]
        if not binaries:
            looked = "".join(f"\n  {b}" for b in searched)
            raise FileNotFoundError(
                f"0 A.D. not found. Looked for:{looked}\n"
                "Pass --game <path to pyrogenesis or the install folder>, set [game] path in the "
                "config, or set the environment variable ZADBOT_GAME."
            )
    binary = binaries[0].resolve()
    data_dir = _data_dir_for(binary)
    if data_dir is None:
        raise FileNotFoundError(
            f"found {binary}, but no data/mods/public/ (mod.json or public.zip) next to it"
        )
    return GameInstall(command=[str(binary)], data_dir=data_dir, version=read_version(data_dir),
                       logs_dir=default_logs_dir())


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


def one_game_at_a_time(game: GameInstall) -> bool:
    """On Windows the engine opens its game files so that no other program
    may read them (_SH_DENYRD in lib/sysdep/os/win/wposix/wfilesystem.cpp).
    A second copy of the game then silently misses mod.zip or public.zip,
    so only one game can run at a time."""
    return sys.platform == "win32" and not game.fake


def running_game_processes() -> list[int]:
    """Process ids of running pyrogenesis.exe (Windows only; [] elsewhere)."""
    if sys.platform != "win32":
        return []
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pyrogenesis.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return parse_tasklist(out)


def parse_tasklist(out: str) -> list[int]:
    pids = []
    for row in csv.reader(out.splitlines()):
        if len(row) > 1 and row[0].lower() == "pyrogenesis.exe" and row[1].isdigit():
            pids.append(int(row[1]))
    return pids


def default_logs_dir() -> Path:
    """Where 0 A.D. writes mainlog.html / interestinglog.html (Paths.cpp)."""
    if sys.platform == "win32":  # pragma: no cover
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "0ad" / "logs"
    if sys.platform == "darwin":  # pragma: no cover
        return Path.home() / "Library" / "Application Support" / "0ad" / "logs"
    xdg = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(xdg) / "0ad" / "log"


def user_data_dir(configured: str = "") -> Path:
    return Path(configured).expanduser() if configured else default_user_data()
