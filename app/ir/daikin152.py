"""Daikin 152-bit A/C protocol (ARC480-family remotes).

Byte layout follows IRremoteESP8266 `Daikin152Protocol`; constant bytes and timings come from the real remote.
Verified on a real unit (cool 27°C fan quiet): the ON state (learned) and the OFF state (learned pulses with
the power bit cleared). The pulse train generated here has not been sent to the unit yet.
"""

MODES = {"cool": 0b011, "heat": 0b100, "dry": 0b010, "auto": 0b000}
FANS = {"auto": 0b1010, "quiet": 0b1011, "min": 3, "low": 4, "med": 5, "high": 6, "max": 7}
MIN_TEMP, MAX_TEMP = 18, 32

# Bytes as captured from the real remote (2026-10-28); 15 and 17 are constants of unknown meaning.
_HEADER = bytes.fromhex("11DA270000")
_BYTE15, _BYTE17 = 0xC5, 0x08
_POWER_BIT = 0x01  # byte 5, bit 0

# Timings measured from the real Daikin remote (one learned frame, 2026-10-28),
# not the IRremoteESP8266 nominal 3492/1718, 433/1529/433 (one space came out ~10% short).
LEADER_BITS = 5
HDR_MARK, HDR_SPACE = 3645, 1870
BIT_MARK, ONE_SPACE, ZERO_SPACE = 440, 1380, 470
LEADER_GAP = 26_700
GAP = 100_000


def build_state(power: bool, mode: str = "cool", temp: int = 25, fan: str = "auto") -> bytes:
    """Return the 19-byte state for the given settings."""
    if mode not in MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {sorted(MODES)}")
    if fan not in FANS:
        raise ValueError(f"unsupported fan {fan!r}; expected one of {sorted(FANS)}")
    if not MIN_TEMP <= temp <= MAX_TEMP:
        raise ValueError(f"temp {temp} out of range {MIN_TEMP}-{MAX_TEMP}")

    state = bytearray(19)
    state[0:5] = _HEADER
    state[5] = (MODES[mode] << 4) | (_POWER_BIT if power else 0)
    state[6] = temp << 1
    state[8] = FANS[fan] << 4  # low nibble = vertical swing, kept off
    state[15] = _BYTE15
    state[17] = _BYTE17
    state[18] = sum(state[:18]) & 0xFF
    return bytes(state)


def to_pulses(state: bytes) -> list[int]:
    """Mark/space durations (µs): a 5-bit zero leader, then the state LSB-first."""
    pulses = [BIT_MARK, ZERO_SPACE] * LEADER_BITS + [BIT_MARK, LEADER_GAP, HDR_MARK, HDR_SPACE]
    for byte in state:
        for i in range(8):
            pulses += [BIT_MARK, ONE_SPACE if (byte >> i) & 1 else ZERO_SPACE]
    pulses += [BIT_MARK, GAP]
    return pulses
