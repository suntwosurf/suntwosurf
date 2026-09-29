"""Sending the bot's actions to the game.

* ``gamepad``  - virtual Xbox 360 pad through ``vgamepad`` (ViGEmBus on
  Windows). Analog steering/throttle/brake: the recommended backend.
* ``keyboard`` - DirectInput key presses through ``pydirectinput``; analog
  values are turned into on/off patterns (sigma-delta), which works but is
  rougher.
* ``udp``      - control packets for ``aorbot fake-game`` (testing without
  the game).
* ``none``     - does nothing (e.g. while a human drives).
"""

from __future__ import annotations

import socket
import struct
import time

from ..config import ControlsConfig
from ..types import Action


def shape_steer(steer: float, deadzone: float) -> float:
    """Jump over the game's stick deadzone so small corrections still count."""
    if deadzone <= 0.0 or abs(steer) < 1e-3:
        return steer
    return (deadzone + (1.0 - deadzone) * abs(steer)) * (1.0 if steer > 0 else -1.0)


class ControlBackend:
    def __init__(self, cfg: ControlsConfig):
        self.cfg = cfg

    def send(self, action: Action) -> None:
        a = action.clipped()
        self._send(Action(shape_steer(a.steer, self.cfg.steer_deadzone), a.throttle, a.brake, a.handbrake))

    def _send(self, action: Action) -> None:
        raise NotImplementedError

    def tap(self, button: str, duration: float = 0.1) -> None:
        """Press and release a button/key (menu navigation)."""
        raise NotImplementedError

    def neutral(self) -> None:
        self._send(Action())

    def close(self) -> None:
        self.neutral()


class NullBackend(ControlBackend):
    def _send(self, action: Action) -> None:
        pass

    def tap(self, button: str, duration: float = 0.1) -> None:
        pass


class GamepadBackend(ControlBackend):
    def __init__(self, cfg: ControlsConfig):
        super().__init__(cfg)
        try:
            import vgamepad as vg
        except ImportError as exc:  # pragma: no cover - platform specific
            raise RuntimeError("gamepad backend needs `pip install vgamepad` (and the ViGEmBus driver on Windows)") from exc
        self.vg = vg
        self.pad = vg.VX360Gamepad()
        self.handbrake = self._button(cfg.handbrake_button)
        self._handbrake_down = False
        self.neutral()

    def _button(self, name: str):
        try:
            return getattr(self.vg.XUSB_BUTTON, "XUSB_GAMEPAD_" + name.upper())
        except AttributeError:
            names = [n[len("XUSB_GAMEPAD_"):] for n in dir(self.vg.XUSB_BUTTON) if n.startswith("XUSB_GAMEPAD_")]
            raise ValueError(f"unknown gamepad button {name!r}; one of {', '.join(names)}") from None

    def _send(self, action: Action) -> None:
        self.pad.left_joystick_float(x_value_float=action.steer, y_value_float=0.0)
        self.pad.right_trigger_float(value_float=action.throttle)
        self.pad.left_trigger_float(value_float=action.brake)
        if action.handbrake != self._handbrake_down:
            (self.pad.press_button if action.handbrake else self.pad.release_button)(button=self.handbrake)
            self._handbrake_down = action.handbrake
        self.pad.update()

    def tap(self, button: str, duration: float = 0.1) -> None:
        b = self._button(button)
        self.pad.press_button(button=b)
        self.pad.update()
        time.sleep(duration)
        self.pad.release_button(button=b)
        self.pad.update()


class KeyboardBackend(ControlBackend):
    def __init__(self, cfg: ControlsConfig):
        super().__init__(cfg)
        try:
            import pydirectinput
        except ImportError as exc:  # pragma: no cover - platform specific
            raise RuntimeError("keyboard backend needs `pip install pydirectinput` (Windows)") from exc
        pydirectinput.PAUSE = 0.0
        self.kb = pydirectinput
        self._down: set[str] = set()
        self._acc = {"steer": 0.0, "throttle": 0.0, "brake": 0.0}

    def _set(self, key: str, down: bool) -> None:
        if down and key not in self._down:
            self.kb.keyDown(key)
            self._down.add(key)
        elif not down and key in self._down:
            self.kb.keyUp(key)
            self._down.discard(key)

    def _pulse(self, channel: str, value: float) -> bool:
        """Sigma-delta: on/off pattern whose average equals ``value``."""
        self._acc[channel] += value
        if self._acc[channel] >= 0.5:
            self._acc[channel] -= 1.0
            return True
        return False

    def _send(self, action: Action) -> None:
        c = self.cfg
        steer_on = self._pulse("steer", abs(action.steer))
        self._set(c.key_left, steer_on and action.steer < 0)
        self._set(c.key_right, steer_on and action.steer > 0)
        self._set(c.key_throttle, self._pulse("throttle", action.throttle))
        self._set(c.key_brake, self._pulse("brake", action.brake))
        self._set(c.key_handbrake, action.handbrake)

    def neutral(self) -> None:
        for key in list(self._down):
            self._set(key, False)
        self._acc = dict.fromkeys(self._acc, 0.0)

    def tap(self, button: str, duration: float = 0.1) -> None:
        self.kb.keyDown(button)
        time.sleep(duration)
        self.kb.keyUp(button)


# --- udp (fake game) --------------------------------------------------------
CONTROL_STRUCT = struct.Struct("<4sIfffBB")
CONTROL_MAGIC = b"AORC"
CMD_DRIVE = 0
CMD_RESTART = 1


def encode_control(seq: int, action: Action, command: int = CMD_DRIVE) -> bytes:
    return CONTROL_STRUCT.pack(CONTROL_MAGIC, seq, action.steer, action.throttle, action.brake, int(action.handbrake), command)


def decode_control(data: bytes) -> tuple[Action, int] | None:
    if len(data) != CONTROL_STRUCT.size or data[:4] != CONTROL_MAGIC:
        return None
    _m, _seq, steer, throttle, brake, handbrake, command = CONTROL_STRUCT.unpack(data)
    return Action(steer, throttle, brake, bool(handbrake)), command


class UdpBackend(ControlBackend):
    """Controls for ``aorbot fake-game``. The button "RESTART" restarts the stage."""

    def __init__(self, cfg: ControlsConfig):
        super().__init__(cfg)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (cfg.udp_host, cfg.udp_port)
        self.seq = 0

    def _emit(self, action: Action, command: int) -> None:
        self.seq += 1
        self.sock.sendto(encode_control(self.seq, action, command), self.addr)

    def _send(self, action: Action) -> None:
        self._emit(action, CMD_DRIVE)

    def tap(self, button: str, duration: float = 0.1) -> None:
        if button.upper() == "RESTART":
            self._emit(Action(), CMD_RESTART)

    def close(self) -> None:
        super().close()
        self.sock.close()


def make_backend(cfg: ControlsConfig) -> ControlBackend:
    backends = {"gamepad": GamepadBackend, "keyboard": KeyboardBackend, "udp": UdpBackend, "none": NullBackend}
    try:
        return backends[cfg.backend](cfg)
    except KeyError:
        raise ValueError(f"unknown controls backend {cfg.backend!r}; one of {', '.join(backends)}") from None
