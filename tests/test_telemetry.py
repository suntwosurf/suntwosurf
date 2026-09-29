import math
import socket

import pytest

from aorbot.config import TelemetryConfig
from aorbot.game.controls import CMD_RESTART, UdpBackend, decode_control, encode_control, shape_steer
from aorbot.game.telemetry import (
    AORBOT_STRUCT,
    FLAG_CAR_FOUND,
    FLAG_PAUSED,
    TelemetryReceiver,
    decode_aorbot,
    decode_forza,
    encode_aorbot,
    encode_forza_fh,
)
from aorbot.config import ControlsConfig
from aorbot.types import Action


def test_aorbot_packet_is_84_bytes_like_the_plugin():
    assert AORBOT_STRUCT.size == 84  # PacketSize in mod/AorBotTelemetry/Plugin.cs


def test_aorbot_unity_axes_to_ground_plane():
    # car at Unity (10, 1, 20) facing +Z, moving at 5 m/s, yawing clockwise (+Y)
    data = encode_aorbot(7, 1.5, (10, 1, 20), (0, 0, 1), (0, 1, 0), (0, 0, 5), (0, 0.4, 0))
    st = decode_aorbot(data)
    assert (st.x, st.y) == (10, 20)
    assert st.heading == pytest.approx(math.pi / 2)
    assert st.speed == pytest.approx(5)
    assert st.yaw_rate == pytest.approx(-0.4)  # clockwise from above = negative
    assert st.upright == pytest.approx(1)
    assert st.race_on


def test_aorbot_flags_and_garbage():
    paused = encode_aorbot(1, 0, (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (0, 0, 0),
                           flags=FLAG_CAR_FOUND | FLAG_PAUSED)
    assert not decode_aorbot(paused).race_on
    no_car = encode_aorbot(1, 0, (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (0, 0, 0), flags=0)
    assert not decode_aorbot(no_car).race_on
    assert decode_aorbot(b"hello") is None


def test_forza_fh_packet_positions_and_yaw_convention():
    data = encode_forza_fh(True, 12.0, (3.0, 1.0, -4.0), yaw=0.0, speed=20.0)
    st = decode_forza(data)
    assert (st.x, st.y, st.speed, st.race_on) == (3.0, -4.0, 20.0, True)
    assert st.heading == pytest.approx(math.pi / 2)  # Unity yaw 0 = facing +Z
    east = decode_forza(encode_forza_fh(True, 0, (0, 0, 0), yaw=math.pi / 2))
    assert math.cos(east.heading) == pytest.approx(1.0)  # yaw 90 deg = facing +X
    assert decode_forza(bytes(100)) is None
    assert decode_forza(bytes(311)) is not None  # FM7 dash size


def test_receiver_gets_packets(free_ports):
    port = free_ports[0]
    rx = TelemetryReceiver(TelemetryConfig(format="aorbot", port=port))
    try:
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(b"junk", ("127.0.0.1", port))
        tx.sendto(encode_aorbot(1, 0, (1, 0, 2), (1, 0, 0), (0, 1, 0), (0, 0, 0), (0, 0, 0)), ("127.0.0.1", port))
        st = rx.wait_fresh(2.0)
        assert st is not None and (st.x, st.y) == (1, 2)
        assert rx.packets == 1 and rx.rejected == 1
        tx.close()
    finally:
        rx.close()


def test_control_packets_roundtrip(free_ports):
    action, cmd = decode_control(encode_control(3, Action(-0.5, 0.25, 0.0, True), CMD_RESTART))
    assert (action.steer, action.throttle, action.handbrake, cmd) == (-0.5, 0.25, True, CMD_RESTART)
    listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen.bind(("127.0.0.1", free_ports[1]))
    listen.settimeout(2.0)
    backend = UdpBackend(ControlsConfig(backend="udp", udp_port=free_ports[1]))
    backend.send(Action(steer=2.0, throttle=0.5))  # clipped to 1.0
    action, cmd = decode_control(listen.recv(64))
    assert action.steer == 1.0 and action.throttle == 0.5 and cmd == 0
    backend.close()
    listen.close()


def test_steer_deadzone_shaping():
    assert shape_steer(0.0, 0.2) == 0.0
    assert shape_steer(0.5, 0.2) == pytest.approx(0.6)
    assert shape_steer(-1.0, 0.2) == pytest.approx(-1.0)
    assert shape_steer(0.3, 0.0) == 0.3
