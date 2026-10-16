"""Mitsubishi Electric 112-bit A/C protocol (also used by some Reetech units).

Byte layout follows IRremoteESP8266 `Mitsubishi112Protocol`; timings are measured from the real remote.
Verified on a real unit: power off (learned) and power on (generated).
"""

MODES = {"cool": 0b011, "heat": 0b001, "auto": 0b111, "dry": 0b010}
FANS = {"quiet": 0b010, "low": 0b011, "med": 0b101, "max": 0b000}
MIN_TEMP, MAX_TEMP = 16, 31

# Bytes 0-5 as captured from the real remote (byte 5 with the power bit cleared).
_BASE = bytes.fromhex("23CB26010020")
_POWER_BIT = 0x04  # byte 5, bit 2
_BYTE8_BASE = 0x40  # bit 6 of byte 8 is set in the captured state; meaning unknown.

# Timings measured from the real Reetech remote (mean of 3 learned frames, 2026-10-16),
# not the IRremoteESP8266 nominal 3450/1696, 450/1250/385 (zero space came out ~17% short).
HDR_MARK, HDR_SPACE = 3150, 1860
BIT_MARK, ONE_SPACE, ZERO_SPACE = 440, 1200, 430
GAP = 100_000


def build_state(power: bool, mode: str = "cool", temp: int = 25, fan: str = "med") -> bytes:
    """Return the 14-byte state for the given settings."""
    if mode not in MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {sorted(MODES)}")
    if fan not in FANS:
        raise ValueError(f"unsupported fan {fan!r}; expected one of {sorted(FANS)}")
    if not MIN_TEMP <= temp <= MAX_TEMP:
        raise ValueError(f"temp {temp} out of range {MIN_TEMP}-{MAX_TEMP}")

    state = bytearray(14)
    state[0:6] = _BASE
    if power:
        state[5] |= _POWER_BIT
    state[6] = MODES[mode]
    state[7] = MAX_TEMP - temp
    state[8] = _BYTE8_BASE | FANS[fan]
    state[13] = sum(state[:13]) & 0xFF
    return bytes(state)


def to_pulses(state: bytes) -> list[int]:
    """Mark/space durations (µs), bytes sent LSB-first, single frame."""
    pulses = [HDR_MARK, HDR_SPACE]
    for byte in state:
        for i in range(8):
            pulses += [BIT_MARK, ONE_SPACE if (byte >> i) & 1 else ZERO_SPACE]
    pulses += [BIT_MARK, GAP]
    return pulses
