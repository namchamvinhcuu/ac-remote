"""IR builders checked against codes captured from the real remotes (2026-10-15)."""

import pytest
from broadlink.remote import data_to_pulses, pulses_to_data

from app.ir import build_packet, coolix, daikin152, mitsubishi112

# Learned by the RM4 mini from the Reetech remote (power off, cool 25°C, fan med).
LEARNED_REETECH_OFF = bytes.fromhex(
    "2600e6005f390e240d250e0d0e0d0d0d0d250e0d0e0c0e250d240e0d0e240d0e0d0d0e250d250d0d0e240d260d0c0e0d0e250d0d0d0e0d250e"
    "0c0d0e0d0e0d0d0e0d0e0d0e0c0d0e0d0e0d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0d0e0d0e0c0e0d0d260d0c0e0d0e250d240e0d0e0d0e0d0d0d"
    "0d0e0d0d0e0d0e240d250e0c0e0d0e0d0d0e0d0e0d240e0d0e240d0e0d0d0e0d0e240d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0e0d0d0e0c0e0d0e"
    "0d0d0e0d0e0d0d0e0c0e0d0e0d0d0e0d0e0d0e0c0d0e0d0e0d0d0e0d0e0d0d0d0d0e0d0e0d0e0d0d0e0d0d0d250e240d0e0d0d0e0d0e0c0e0d"
    "0d260d000d05"
)
# Learned by the RM4 mini from the Reetech remote on 2026-10-16 (power on, cool, fan med).
# Today's remote sends byte 8 = 0x05 (bit 6 clear); the encoder keeps 0x45 and the unit accepts both,
# so these two are timing ground truth, not byte ground truth.
LEARNED_REETECH_ON_COOL24 = bytes.fromhex(
    "2600e60060390d250d240e0d0e0d0d0e0d240e0d0e0d0e240d250e0d0e240d0e0d0e0d240e240d0e0d250e240d0e0d0e0d240e0d0d0e0d240e0d"
    "0e0d0e0c0e0d0d0e0d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0e0d0c0f0c0e0d0e0d0d250d0d0e0d0e240d0e0d0e0d240e250d0e0d0d0d0d0e0d0e0d"
    "0e0d0d250d250e240d0e0d0d0e0c0e0d0e0d0d250d0e0d250e0c0d0e0d0e0d0e0d0d0e0d0e0c0e0d0d0e0d0e0d0e0d0d0e0c0e0d0e0d0d0e0d0e"
    "0d0d0d0d0e0d0e0d0e0d0d0d0e0d0d0e0d0d0e0d0e0d0e0c0d0e0d0e0d0d0e0d0e0c0e0d0e0d0d0e0d0e0d0d0e0c0e250d0e0e0c0d250e0d0e00"
    "0d05"
)
LEARNED_REETECH_ON_COOL21 = bytes.fromhex(
    "2600e60061380e240d250d0d0e0d0e0d0e240d0e0d0d0e240d260d0c0e250e0d0d0d0d250e240d0e0d240e250e0d0d0d0d250e0d0e0c0f240d0e"
    "0d0c0f0c0e0d0e0d0d0e0d0e0d0d0d0d0e0d0e0d0e0d0d0d0d0e0d0e0d0d0e0d0e240d0e0d0e0d240e0d0d0e0d240e240e0d0d0e0d0e0d0e0d0c"
    "0f0c0e0d0e240e0d0d250e0d0d0d0d0e0d0e0d240e0d0d260d0c0f0c0e0d0e0d0d0e0d0d0d0e0d0e0d0d0e0d0e0d0d0d0d0e0d0e0d0d0e0d0e0c"
    "0e0d0d0e0d0e0d0e0d0c0e0d0e0d0e0d0d0e0d0e0d0d0d0d0e0d0e0d0e0d0d0d0d0e0d0e0d0d0e0d0e240d250e0c0e250d0e0d0d0d250e0d0e00"
    "0d05"
)
# (frame, power, temp) for every real Reetech capture; all are cool / fan med.
REETECH_FRAMES = {
    "off_cool25": (LEARNED_REETECH_OFF, False, 25),
    "on_cool24": (LEARNED_REETECH_ON_COOL24, True, 24),
    "on_cool21": (LEARNED_REETECH_ON_COOL21, True, 21),
}
TICK = 32.84  # µs per BroadLink tick
FRAME_PULSES = 2 + 112 * 2 + 1  # header + 112 bits + trailing mark (the final gap is excluded)
# Learned from the Midea remote (power off).
LEARNED_MIDEA_OFF = bytes.fromhex(
    "2600ca008c8f10341211113411351012101310341112101211341112111111351035101210351012123311341135103510121134103511341111"
    "121111121013103411121012113411341233111211121012111210121013101211111233113411351035103510a88c8f10351013103412331112"
    "111111351012111210351012101310341233121111351012103510351035113411111234103510351111111112111111113411121112103510"
    "351035111111121111111112111112101310121035103511341134113411000d05"
)
# Learned by the home RM4 mini from the Daikin ARC480-family remote on 2026-10-28 (power on, cool 27°C, fan quiet).
# Replayed byte-identical, it turned the real unit on.
LEARNED_DAIKIN_ON_COOL27 = bytes.fromhex(
    "260044010e0d0f0e0e0e0d0f0d0e0e00032d6f390d2a0e0e0e0e0d0f0d2a0d0f0e0e0e0e0e0e0d2b0d0e0e2a0d2a0e0e0e290f2a0d2a0e2a0d2a"
    "0e0e0f0d0e2a0e0e0d0f0d0f0d0e0e0e0d0f0d0f0d0e0e0e0e0e0e0f0d0e0e0e0d0f0d0f0d0e0e0e0d0f0d2a0f0d0e0e0e0e0e2a0d2a0e0e0e0e"
    "0d0f0d2a0e2b0d0e0e2a0d2a0e0e0d0f0d0f0d0e0f0d0e0f0d0f0d0e0e0e0d0f0d0f0d0e0e0e0d0f0d2a0f2a0d0e0e2a0d0f0d0e0e0e0e0e0d0f"
    "0e0d0f0e0d0f0d0f0d0e0e0e0d0f0d0f0d0e0e0e0d0f0e0e0e0e0e0e0d0f0d0e0e0e0e0e0d0f0d0e0e0e0e0e0d0f0e0e0e0e0e0e0d0f0d0e0e0e"
    "0e0e0d0f0d0e0e0e0d0f0e0f0d0e0e0e0d0f0d0f0d0e0e0e0d0f0d0f0d2a0e0e0e2a0e0e0d0f0d0e0e2a0d2a0e0e0f0d0e0f0d0e0e0e0d0f0d0f"
    "0d0e0e0e0d0f0d0f0d2b0d0f0d0f0d0e0e0e0d0f0d2a0e2a0d0f0d2b0d2a0e2a0d2a0e000d05"
)
# The two Daikin states proven on hardware (2026-10-28): the learned ON frame and the OFF state
# (sent with the learned pulses, power bit cleared); this encoder's own pulse train has not been sent yet.
DAIKIN_VERIFIED_ON = "11da270000313600b0000000000000c50008f6"
DAIKIN_VERIFIED_OFF = "11da270000303600b0000000000000c50008f5"
# 5-bit zero leader (10 pulses) + leader mark + leader gap, then the header mark/space: the state starts at 14.
DAIKIN_STATE_START = 2 * 5 + 2 + 2
DAIKIN_FRAME_PULSES = DAIKIN_STATE_START + 152 * 2 + 1  # + trailing mark (the final gap is excluded)


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


# --- Mitsubishi112 timings vs the real Reetech remote (2026-10-16) ---
# The encoder now uses the timings measured from the real remote instead of the IRremoteESP8266
# nominal ones (zero space was ~17% short); these lock them so an edit cannot silently drift back.

def _ticks(pulses: list[int]) -> list[int]:
    return [round(p / TICK) for p in pulses]


def _assert_within_two_ticks(generated: list[int], real: list[int], frame: int = FRAME_PULSES) -> None:
    gen, ref = _ticks(generated[:frame]), _ticks(real[:frame])
    off = [(i, g, r) for i, (g, r) in enumerate(zip(gen, ref)) if abs(g - r) > 2]
    assert not off, f"pulses > 2 ticks from the real remote (index, generated, real): {off[:10]}"


def _classes(pulses: list[int]) -> dict[str, list[int]]:
    """Split one frame into header mark/space, bit marks, one spaces and zero spaces (µs)."""
    spaces = pulses[3:FRAME_PULSES:2]
    return {
        "hdr_mark": [pulses[0]],
        "hdr_space": [pulses[1]],
        "bit_mark": pulses[2:FRAME_PULSES:2],
        "one_space": [s for s in spaces if s > 900],
        "zero_space": [s for s in spaces if s <= 900],
    }


def test_mitsubishi112_off_packet_matches_learned_remote_pulse_for_pulse():
    generated = data_to_pulses(build_packet("mitsubishi112", False, "cool", 25, "med"))
    real = data_to_pulses(LEARNED_REETECH_OFF)
    assert len(generated) == len(real) == FRAME_PULSES + 1
    _assert_within_two_ticks(generated, real)


@pytest.mark.parametrize("name", sorted(REETECH_FRAMES))
def test_mitsubishi112_timings_match_every_real_frame(name):
    # Re-encode the real frame's own bits, so only the timings are compared (byte 8 differs today).
    real = data_to_pulses(REETECH_FRAMES[name][0])
    state = _lsb_bytes(_bits(real, 2, 112))
    generated = data_to_pulses(pulses_to_data(mitsubishi112.to_pulses(state)))
    assert len(generated) == len(real)
    _assert_within_two_ticks(generated, real)


@pytest.mark.parametrize("name", sorted(REETECH_FRAMES))
def test_mitsubishi112_packet_timings_inside_real_remote_ranges(name):
    frame, power, temp = REETECH_FRAMES[name]
    generated = _classes(data_to_pulses(build_packet("mitsubishi112", power, "cool", temp, "med")))
    real = _classes(data_to_pulses(frame))
    # Header: one sample per frame, so check against the spread of all three captures.
    every = [_classes(data_to_pulses(f)) for f, _, _ in REETECH_FRAMES.values()]
    for key in ("hdr_mark", "hdr_space"):
        seen = [c[key][0] for c in every]
        assert min(seen) <= generated[key][0] <= max(seen), (key, generated[key][0], seen)
    for key in ("bit_mark", "one_space", "zero_space"):
        assert len(set(generated[key])) == 1, key
        value = generated[key][0]
        assert min(real[key]) <= value <= max(real[key]), (key, value, min(real[key]), max(real[key]))


@pytest.mark.parametrize("name,expected", [
    ("on_cool24", "23cb260100240307050000000048"),
    ("on_cool21", "23cb26010024030a05000000004b"),
])
def test_reetech_on_frames_differ_from_encoder_only_in_byte8_bit6(name, expected):
    frame, power, temp = REETECH_FRAMES[name]
    real = _lsb_bytes(_bits(data_to_pulses(frame), 2, 112))
    assert real.hex() == expected
    assert real[13] == sum(real[:13]) & 0xFF
    generated = mitsubishi112.build_state(power, "cool", temp, "med")
    assert real[:8] == generated[:8] and real[9:13] == generated[9:13]
    assert real[8] | mitsubishi112._BYTE8_BASE == generated[8]


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


# --- Daikin152 (learned from the real ARC480-family remote, 2026-10-28) ---

def _daikin_state(pulses: list[int]) -> bytes:
    return _lsb_bytes(_bits(pulses, DAIKIN_STATE_START, 152))


def _daikin_expected(power: bool, mode: int, temp: int, fan: int) -> bytes:
    """The learned frame with only power/mode (byte 5), temp (6), fan (8) and checksum (18) replaced."""
    state = bytearray.fromhex(DAIKIN_VERIFIED_ON)
    state[5] = (mode << 4) | int(power)
    state[6] = temp << 1
    state[8] = fan << 4
    state[18] = sum(state[:18]) & 0xFF
    return bytes(state)


def test_daikin152_learned_frame_shape_leader_gap_header():
    pulses = data_to_pulses(LEARNED_DAIKIN_ON_COOL27)
    assert len(pulses) == DAIKIN_FRAME_PULSES + 1
    assert _bits(pulses, 0, 5) == [0] * 5  # 5-bit zero leader
    assert pulses[11] > 20_000  # leader gap (~26.7 ms)
    assert pulses[12] > 3000 and pulses[13] > 1600  # header mark/space


def test_daikin152_learned_on_decodes_to_encoder_state():
    learned = _daikin_state(data_to_pulses(LEARNED_DAIKIN_ON_COOL27))
    assert learned.hex() == DAIKIN_VERIFIED_ON
    assert daikin152.build_state(True, "cool", 27, "quiet") == learned


def test_daikin152_off_is_the_verified_state():
    # This exact state (learned pulses with the power bit cleared, checksum f5) turned the real unit off.
    assert daikin152.build_state(False, "cool", 27, "quiet").hex() == DAIKIN_VERIFIED_OFF


def test_daikin152_on_packet_matches_learned_remote_pulse_for_pulse():
    generated = data_to_pulses(build_packet("daikin152", True, "cool", 27, "quiet"))
    real = data_to_pulses(LEARNED_DAIKIN_ON_COOL27)
    assert len(generated) == len(real) == DAIKIN_FRAME_PULSES + 1
    _assert_within_two_ticks(generated, real, DAIKIN_FRAME_PULSES)
    assert generated[-1] >= 100_000 - 2 * TICK  # long final gap kept (value excluded from the tick check)


def test_daikin152_off_packet_differs_from_learned_only_in_power_and_checksum_bits():
    generated = data_to_pulses(build_packet("daikin152", False, "cool", 27, "quiet"))
    real = data_to_pulses(LEARNED_DAIKIN_ON_COOL27)
    assert len(generated) == len(real)
    assert _daikin_state(generated).hex() == DAIKIN_VERIFIED_OFF
    gen_bits = _bits(generated, DAIKIN_STATE_START, 152)
    real_bits = _bits(real, DAIKIN_STATE_START, 152)
    # 0xf6 -> 0xf5 flips checksum bits 0 and 1; power is byte 5 bit 0.
    assert [i for i, (g, r) in enumerate(zip(gen_bits, real_bits)) if g != r] == [5 * 8, 18 * 8, 18 * 8 + 1]


# (power, mode, temp, fan) -> (mode nibble, temp in byte 6, fan nibble) per IRremoteESP8266 Daikin152.
DAIKIN_CASES = [
    ((True, "cool", 27, "quiet"), (0b011, 27, 0b1011)),
    ((True, "cool", 18, "auto"), (0b011, 18, 0b1010)),
    ((False, "heat", 32, "max"), (0b100, 32, 7)),
    ((True, "heat", 24, "min"), (0b100, 24, 3)),
    ((True, "auto", 22, "low"), (0b000, 22, 4)),
    ((True, "auto", 25, "med"), (0b000, 25, 5)),
    ((True, "cool", 30, "high"), (0b011, 30, 6)),
    ((True, "dry", 26, "auto"), (0b010, 26, 0b1010)),  # dry sends the commanded temp (IRac::daikin152)
]


@pytest.mark.parametrize("args,expected", DAIKIN_CASES, ids=lambda v: "-".join(map(str, v)))
def test_daikin152_fields_and_checksum(args, expected):
    state = daikin152.build_state(*args)
    assert state == _daikin_expected(args[0], *expected)
    assert len(state) == 19
    assert state[18] == sum(state[:18]) & 0xFF


@pytest.mark.parametrize("args,_expected", DAIKIN_CASES, ids=lambda v: "-".join(map(str, v)))
def test_daikin152_pulses_roundtrip(args, _expected):
    state = daikin152.build_state(*args)
    pulses = daikin152.to_pulses(state)
    assert len(pulses) == DAIKIN_FRAME_PULSES + 1
    assert _bits(pulses, 0, 5) == [0] * 5
    assert _daikin_state(pulses) == state


@pytest.mark.parametrize("temp", [18, 25, 32])
@pytest.mark.parametrize("power", [True, False])
def test_daikin152_dry_sends_the_commanded_temp(temp, power):
    # IRremoteESP8266 IRac::daikin152 calls setMode() then setTemp(), so dry carries the commanded temp.
    assert daikin152.build_state(power, "dry", temp, "auto")[6] == temp << 1
    assert daikin152.build_state(power, "cool", temp, "auto")[6] == temp << 1


@pytest.mark.parametrize("kwargs", [
    {"temp": 17}, {"temp": 33}, {"mode": "fan"}, {"fan": "turbo"}, {"fan": "bogus"},
    {"power": False, "temp": 99}, {"power": False, "fan": "bogus"}, {"power": False, "mode": "fan"},
])
def test_daikin152_rejects_invalid(kwargs):
    args = {"power": True, "mode": "cool", "temp": 25, "fan": "auto"} | kwargs
    with pytest.raises(ValueError):
        daikin152.build_state(**args)


@pytest.mark.parametrize("temp", [18, 32])
def test_daikin152_temp_range_boundaries_are_accepted(temp):
    assert daikin152.build_state(True, "cool", temp, "auto")[6] == temp << 1


def test_daikin152_packet_is_built_from_daikin_pulses():
    packet = build_packet("daikin152", True, "heat", 22, "high")
    assert packet[0] == 0x26
    assert int.from_bytes(packet[2:4], "little") == len(packet) - 4
    expected = daikin152.to_pulses(daikin152.build_state(True, "heat", 22, "high"))
    decoded = data_to_pulses(packet)
    assert len(decoded) == len(expected)
    assert all(abs(d - e) < 66 for d, e in zip(decoded, expected))


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
