"""IR builders checked against codes captured from the real remotes (2026-10-15)."""

import pytest
from broadlink.remote import data_to_pulses

from app.ir import build_packet, coolix, mitsubishi112

# Learned by the RM4 mini from the Reetech remote (power off, cool 25°C, fan med).
LEARNED_REETECH_OFF = bytes.fromhex(
    "2600e6005f390e240d250e0d0e0d0d0d0d250e0d0e0c0e250d240e0d0e240d0e0d0d0e250d250d0d0e240d260d0c0e0d0e250d0d0d0e0d250e"
    "0c0d0e0d0e0d0d0e0d0e0d0e0c0d0e0d0e0d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0d0e0d0e0c0e0d0d260d0c0e0d0e250d240e0d0e0d0e0d0d0d"
    "0d0e0d0d0e0d0e240d250e0c0e0d0e0d0d0e0d0e0d240e0d0e240d0e0d0d0e0d0e240d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0e0d0d0e0c0e0d0e"
    "0d0d0e0d0e0d0d0e0c0e0d0e0d0d0e0d0e0d0e0c0d0e0d0e0d0d0e0d0e0d0d0d0d0e0d0e0d0e0d0d0e0d0d0d250e240d0e0d0d0e0d0e0c0e0d"
    "0d260d000d05"
)
# Learned from the Midea remote (power off).
LEARNED_MIDEA_OFF = bytes.fromhex(
    "2600ca008c8f10341211113411351012101310341112101211341112111111351035101210351012123311341135103510121134103511341111"
    "121111121013103411121012113411341233111211121012111210121013101211111233113411351035103510a88c8f10351013103412331112"
    "111111351012111210351012101310341233121111351012103510351035113411111234103510351111111112111111113411121112103510"
    "351035111111121111111112111112101310121035103511341134113411000d05"
)


def _bits(pulses: list[int], start: int, count: int) -> list[int]:
    """Read `count` bits from mark/space pairs starting at `start` (space > 900 µs = 1)."""
    return [1 if pulses[start + 2 * i + 1] > 900 else 0 for i in range(count)]


def _lsb_bytes(bits: list[int]) -> bytes:
    return bytes(sum(b << i for i, b in enumerate(bits[n:n + 8])) for n in range(0, len(bits), 8))


def _msb_bytes(bits: list[int]) -> bytes:
    return bytes(sum(b << (7 - i) for i, b in enumerate(bits[n:n + 8])) for n in range(0, len(bits), 8))


# --- Mitsubishi112 ---

def test_mitsubishi112_off_matches_learned_remote():
    learned = _lsb_bytes(_bits(data_to_pulses(LEARNED_REETECH_OFF), 2, 112))
    assert mitsubishi112.build_state(False, "cool", 25, "med") == learned


def test_mitsubishi112_on_is_the_verified_state():
    # This exact state turned the real unit on.
    assert mitsubishi112.build_state(True, "cool", 25, "med").hex().upper() == "23CB26010024030645000000" + "0087"


@pytest.mark.parametrize("temp", [16, 24, 31])
def test_mitsubishi112_temp_and_checksum(temp):
    state = mitsubishi112.build_state(True, "heat", temp, "max")
    assert state[7] == 31 - temp
    assert state[6] == mitsubishi112.MODES["heat"]
    assert state[13] == sum(state[:13]) & 0xFF


def test_mitsubishi112_pulses_roundtrip():
    state = mitsubishi112.build_state(True, "dry", 22, "low")
    assert _lsb_bytes(_bits(mitsubishi112.to_pulses(state), 2, 112)) == state


@pytest.mark.parametrize("kwargs", [{"temp": 15}, {"temp": 32}, {"mode": "fan"}, {"fan": "auto"}])
def test_mitsubishi112_rejects_invalid(kwargs):
    args = {"power": True, "mode": "cool", "temp": 25, "fan": "med"} | kwargs
    with pytest.raises(ValueError):
        mitsubishi112.build_state(**args)


# --- Coolix ---

def test_coolix_off_matches_learned_remote():
    pulses = data_to_pulses(LEARNED_MIDEA_OFF)
    frame = _msb_bytes(_bits(pulses, 2, 48))
    assert frame == bytes.fromhex("B24D7B84E01F")
    assert coolix.build_code(False) == int.from_bytes(frame[0::2], "big")


def test_coolix_on_is_the_verified_code():
    # This exact code turned the real Midea units on.
    assert coolix.build_code(True, "cool", 25, "auto") == 0xB2BFC0


def test_coolix_auto_and_dry_force_fan_auto0():
    for mode in ("auto", "dry"):
        code = coolix.build_code(True, mode, 24, "max")
        assert (code >> 13) & 0b111 == 0b000


def test_coolix_pulses_send_byte_and_inverse_twice():
    code = coolix.build_code(True, "heat", 28, "min")
    pulses = coolix.to_pulses(code)
    frame_len = 2 + 48 * 2 + 2
    assert len(pulses) == 2 * frame_len
    for start in (2, frame_len + 2):
        raw = _msb_bytes(_bits(pulses, start, 48))
        assert raw[0::2] == code.to_bytes(3, "big")
        assert all(a ^ b == 0xFF for a, b in zip(raw[0::2], raw[1::2]))


@pytest.mark.parametrize("kwargs", [{"temp": 16}, {"temp": 31}, {"mode": "fan"}, {"fan": "quiet"}])
def test_coolix_rejects_invalid(kwargs):
    args = {"power": True, "mode": "cool", "temp": 25, "fan": "auto"} | kwargs
    with pytest.raises(ValueError):
        coolix.build_code(**args)


# --- BroadLink packet ---

@pytest.mark.parametrize("protocol,fan", [("mitsubishi112", "med"), ("coolix", "auto")])
def test_packet_timings_survive_broadlink_encoding(protocol, fan):
    packet = build_packet(protocol, True, "cool", 25, fan)
    assert packet[0] == 0x26
    assert int.from_bytes(packet[2:4], "little") == len(packet) - 4
    builder = mitsubishi112 if protocol == "mitsubishi112" else coolix
    expected = builder.to_pulses(builder.build_state(True, "cool", 25, fan) if protocol == "mitsubishi112"
                                 else builder.build_code(True, "cool", 25, fan))
    decoded = data_to_pulses(packet)
    assert len(decoded) == len(expected)
    # One BroadLink tick is 32.84 µs; floor + int truncation loses < 2 ticks.
    assert all(abs(d - e) < 66 for d, e in zip(decoded, expected))


def test_packet_rejects_unknown_protocol():
    with pytest.raises(ValueError):
        build_packet("nec", True, "cool", 25, "auto")
