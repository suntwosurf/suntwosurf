"""Telemetry from the game over UDP.

Two packet formats are understood:

* ``aorbot`` - the small packet sent by the BepInEx plugin in ``mod/``.
* ``forza``  - Forza "dash" packets (FH4/FH5 324 bytes, FM7 311, FM 2023 331),
  as sent e.g. by the art-of-sim-rally mod's SimHub telemetry option.
"""

from __future__ import annotations

import math
import socket
import struct
import threading
import time
from typing import Callable

from ..config import TelemetryConfig
from ..types import CarState

# --- aorbot packet --------------------------------------------------------
# magic, version, seq, game time, position xyz, forward xyz, up xyz,
# velocity xyz, angular velocity xyz (all Unity world space), scene index, flags
AORBOT_STRUCT = struct.Struct("<4sIIf3f3f3f3f3fiI")
AORBOT_MAGIC = b"AORT"
AORBOT_VERSION = 1
FLAG_CAR_FOUND = 1
FLAG_PAUSED = 2


def encode_aorbot(seq, t, pos, fwd, up, vel, angvel, scene=0, flags=FLAG_CAR_FOUND) -> bytes:
    return AORBOT_STRUCT.pack(AORBOT_MAGIC, AORBOT_VERSION, seq, t, *pos, *fwd, *up, *vel, *angvel, scene, flags)


def decode_aorbot(data: bytes) -> CarState | None:
    if len(data) != AORBOT_STRUCT.size or data[:4] != AORBOT_MAGIC:
        return None
    f = AORBOT_STRUCT.unpack(data)
    if f[1] != AORBOT_VERSION:
        return None
    t = f[3]
    px, _py, pz = f[4:7]
    fx, _fy, fz = f[7:10]
    up_y = f[11]
    vx, _vy, vz = f[13:16]
    wy = f[17]
    flags = f[20]
    # Unity is left-handed with Y up: seen from above, (X, Z) is an ordinary
    # counter-clockwise frame and a positive rotation about +Y is clockwise.
    return CarState(
        t=t,
        x=px,
        y=pz,
        heading=math.atan2(fz, fx),
        speed=math.hypot(vx, vz),
        vx=vx,
        vy=vz,
        yaw_rate=-wy,
        upright=up_y,
        race_on=bool(flags & FLAG_CAR_FOUND) and not flags & FLAG_PAUSED,
    )


# --- Forza dash packet ----------------------------------------------------
_DASH_BASE = {311: 232, 331: 232, 324: 244}  # FM7, FM 2023, FH4/FH5


def decode_forza(data: bytes, yaw_sign: float = -1.0, yaw_offset_deg: float = 90.0) -> CarState | None:
    base = _DASH_BASE.get(len(data))
    if base is None:
        return None
    race_on = struct.unpack_from("<i", data, 0)[0] != 0
    timestamp_ms = struct.unpack_from("<I", data, 4)[0]
    wy = struct.unpack_from("<f", data, 48)[0]
    yaw, pitch, roll = struct.unpack_from("<3f", data, 56)
    px, _py, pz, speed = struct.unpack_from("<4f", data, base)
    heading = yaw_sign * yaw + math.radians(yaw_offset_deg)
    return CarState(
        t=timestamp_ms / 1000.0,
        x=px,
        y=pz,
        heading=heading,
        speed=abs(speed),
        vx=abs(speed) * math.cos(heading),
        vy=abs(speed) * math.sin(heading),
        yaw_rate=-wy,
        upright=math.cos(pitch) * math.cos(roll),
        race_on=race_on,
    )


def encode_forza_fh(race_on, t, pos, yaw, pitch=0.0, roll=0.0, speed=0.0, yaw_rate_unity=0.0) -> bytes:
    """Minimal FH4/FH5-sized packet (only the fields decode_forza reads)."""
    buf = bytearray(324)
    struct.pack_into("<iI", buf, 0, int(race_on), int(t * 1000) & 0xFFFFFFFF)
    struct.pack_into("<f", buf, 48, yaw_rate_unity)
    struct.pack_into("<3f", buf, 56, yaw, pitch, roll)
    struct.pack_into("<4f", buf, 244, pos[0], pos[1], pos[2], speed)
    struct.pack_into("<f", buf, 308, t)
    return bytes(buf)


def make_decoder(cfg: TelemetryConfig) -> Callable[[bytes], CarState | None]:
    if cfg.format == "aorbot":
        return decode_aorbot
    if cfg.format == "forza":
        return lambda data: decode_forza(data, cfg.yaw_sign, cfg.yaw_offset_deg)
    raise ValueError(f"unknown telemetry format {cfg.format!r} (aorbot | forza)")


# --- receiver --------------------------------------------------------------
class TelemetryReceiver:
    """Listens in a background thread and keeps the newest state.

    ``state.t`` is replaced by the local monotonic clock at arrival, so timing
    does not depend on how a particular mod fills its time fields.
    """

    def __init__(self, cfg: TelemetryConfig):
        self.decode = make_decoder(cfg)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((cfg.host, cfg.port))
        self.sock.settimeout(0.2)
        self.packets = 0
        self.rejected = 0
        self._state: CarState | None = None
        self._stamp = 0.0
        self._cond = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            state = self.decode(data)
            if state is None:
                self.rejected += 1
                continue
            now = time.monotonic()
            state.t = now
            with self._cond:
                self._state, self._stamp = state, now
                self.packets += 1
                self._cond.notify_all()

    def latest(self) -> tuple[CarState | None, float]:
        """Newest state and its age in seconds (inf if nothing arrived yet)."""
        with self._cond:
            if self._state is None:
                return None, math.inf
            return self._state, time.monotonic() - self._stamp

    def wait_fresh(self, timeout: float) -> CarState | None:
        """Block until a packet newer than this call arrives (or timeout)."""
        start = time.monotonic()
        with self._cond:
            self._cond.wait_for(lambda: self._stamp > start, timeout=timeout)
            return self._state if self._stamp > start else None

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        finally:
            self._thread.join(timeout=1.0)
