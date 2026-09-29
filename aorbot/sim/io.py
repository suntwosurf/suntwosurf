"""The simulator as a VehicleIO, plus a fake game that speaks the same UDP
protocols as the real bridge (for testing GameIO without art of rally)."""

from __future__ import annotations

import math
import socket
import threading
import time

import numpy as np

from ..game.controls import CMD_RESTART, decode_control
from ..game.telemetry import FLAG_CAR_FOUND, FLAG_PAUSED, encode_aorbot, encode_forza_fh
from ..types import Action, CarState
from .car import SimCar, SimCarParams
from .stage import SimStage

PHYSICS_DT = 0.01


class SimWorld:
    """Car on a stage: off-road slows it, the trees beyond the verge stop it."""

    def __init__(self, stage: SimStage, weather: str = "dry", params: SimCarParams | None = None):
        self.stage = stage
        self.car = SimCar(params, weather)
        self.restart()

    def restart(self) -> None:
        line = self.stage.centerline
        self.car.place(*line.xy[0], float(line.heading[0]))
        self.t = 0.0
        self.s_hint = 0.0
        self.crashed = False

    def advance(self, action: Action, duration: float) -> None:
        line = self.stage.centerline
        steps = max(1, int(round(duration / PHYSICS_DT)))
        for _ in range(steps):
            if not self.crashed:
                p = line.project(self.car.x, self.car.y, s_hint=self.s_hint, window=40.0)
                self.s_hint = p.s
                if abs(p.offset) > self.stage.crash_offset:
                    self.crashed = True  # into the trees
                    self.car.v = 0.0
                else:
                    self.car.step(action, PHYSICS_DT, offroad=abs(p.offset) > self.stage.half_width)
            self.t += PHYSICS_DT

    def state(self) -> CarState:
        c = self.car
        return CarState(
            t=self.t,
            x=c.x,
            y=c.y,
            heading=c.heading,
            speed=c.v,
            vx=c.v * math.cos(c.heading),
            vy=c.v * math.sin(c.heading),
            yaw_rate=c.yaw_rate,
        )


class SimIO:
    """Runs as fast as the CPU allows (no real-time pacing)."""

    def __init__(self, stage: SimStage, weather: str = "dry", dt: float = 1.0 / 30.0,
                 params: SimCarParams | None = None, position_noise: float = 0.0, seed: int = 0):
        self.world = SimWorld(stage, weather, params)
        self.dt = dt
        self.position_noise = position_noise
        self.rng = np.random.default_rng(seed)

    def _observe(self) -> CarState:
        st = self.world.state()
        if self.position_noise:
            st.x += float(self.rng.normal(0, self.position_noise))
            st.y += float(self.rng.normal(0, self.position_noise))
        return st

    def reset(self) -> CarState:
        self.world.restart()
        return self._observe()

    def step(self, action: Action) -> CarState:
        self.world.advance(action.clipped(), self.dt)
        return self._observe()

    def release(self) -> None:
        pass


class FakeGame:
    """Real-time simulator behind UDP: listens for control packets and sends
    telemetry like the game plugin would. A restart command puts the car back
    at the start after ``load_time`` s (telemetry says paused meanwhile)."""

    def __init__(self, stage: SimStage, weather: str = "dry", telemetry_addr=("127.0.0.1", 47800),
                 control_addr=("127.0.0.1", 47801), fmt: str = "aorbot", rate: float = 60.0,
                 time_scale: float = 1.0, load_time: float = 0.5):
        self.world = SimWorld(stage, weather)
        self.telemetry_addr = telemetry_addr
        self.fmt = fmt
        self.rate = rate
        self.time_scale = time_scale
        self.load_time = load_time
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.inp.bind(control_addr)
        self.inp.setblocking(False)
        self.action = Action()
        self.loading_until = 0.0
        self.restarts = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _poll_controls(self) -> None:
        while True:
            try:
                data = self.inp.recv(256)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            decoded = decode_control(data)
            if decoded is None:
                continue
            action, command = decoded
            if command == CMD_RESTART:
                self.world.restart()
                self.action = Action()
                self.restarts += 1
                self.loading_until = time.monotonic() + self.load_time
            else:
                self.action = action

    def _packet(self, seq: int, paused: bool) -> bytes:
        c = self.world.car
        pos = (c.x, 0.0, c.y)
        if self.fmt == "forza":
            yaw = math.pi / 2 - c.heading  # Unity euler Y convention
            return encode_forza_fh(not paused, self.world.t, pos, yaw, speed=c.v, yaw_rate_unity=-c.yaw_rate)
        fwd = (math.cos(c.heading), 0.0, math.sin(c.heading))
        vel = (c.v * fwd[0], 0.0, c.v * fwd[2])
        flags = FLAG_CAR_FOUND | (FLAG_PAUSED if paused else 0)
        return encode_aorbot(seq, self.world.t, pos, fwd, (0.0, 1.0, 0.0), vel, (0.0, -c.yaw_rate, 0.0), 0, flags)

    def run(self) -> None:
        period = 1.0 / self.rate
        seq = 0
        next_tick = time.monotonic()
        while not self._stop.is_set():
            self._poll_controls()
            paused = time.monotonic() < self.loading_until
            if not paused:
                self.world.advance(self.action, period * self.time_scale)
            seq += 1
            self.out.sendto(self._packet(seq, paused), self.telemetry_addr)
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    def start(self) -> "FakeGame":
        self._thread = threading.Thread(target=self.run, name="fake-game", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.inp.close()
        self.out.close()
