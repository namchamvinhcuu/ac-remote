"""Coolix 24-bit A/C protocol (Midea and many OEM units).

Bit layout and timings follow IRremoteESP8266 `CoolixProtocol`.
Verified on a real Midea unit: power off and power on (cool 25°C, fan auto).
"""

MODES = {"cool": 0b00, "dry": 0b01, "auto": 0b10, "heat": 0b11}
FANS = {"auto": 0b101, "min": 0b100, "med": 0b010, "max": 0b001}
_FAN_AUTO0 = 0b000  # the only fan value allowed in auto/dry modes
TEMP_MAP = {
    17: 0b0000, 18: 0b0001, 19: 0b0011, 20: 0b0010, 21: 0b0110, 22: 0b0111, 23: 0b0101,
    24: 0b0100, 25: 0b1100, 26: 0b1101, 27: 0b1001, 28: 0b1000, 29: 0b1010, 30: 0b1011,
}
OFF = 0xB27BE0
_SENSOR_IGNORE = 0b11111

_TICK = 276
HDR_MARK, HDR_SPACE = 17 * _TICK, 16 * _TICK
BIT_MARK, ONE_SPACE, ZERO_SPACE = 2 * _TICK, 6 * _TICK, 2 * _TICK
MIN_GAP = 19 * _TICK
FINAL_GAP = 100_000


def build_code(power: bool, mode: str = "cool", temp: int = 25, fan: str = "auto") -> int:
    """Return the 24-bit command for the given settings (validated even for power off)."""
    if mode not in MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {sorted(MODES)}")
    if fan not in FANS:
        raise ValueError(f"unsupported fan {fan!r}; expected one of {sorted(FANS)}")
    if temp not in TEMP_MAP:
        raise ValueError(f"temp {temp} out of range {min(TEMP_MAP)}-{max(TEMP_MAP)}")
    if not power:
        return OFF

    fan_bits = _FAN_AUTO0 if mode in ("auto", "dry") else FANS[fan]
    return 0xB20000 | (fan_bits << 13) | (_SENSOR_IGNORE << 8) | (TEMP_MAP[temp] << 4) | (MODES[mode] << 2)


def to_pulses(code: int) -> list[int]:
    """Mark/space durations (µs): each byte MSB-first then its inverse; frame sent twice."""
    frame = [HDR_MARK, HDR_SPACE]
    for byte in code.to_bytes(3, "big"):
        for value in (byte, byte ^ 0xFF):
            for i in range(7, -1, -1):
                frame += [BIT_MARK, ONE_SPACE if (value >> i) & 1 else ZERO_SPACE]
    frame += [BIT_MARK, MIN_GAP]
    pulses = frame * 2
    pulses[-1] = FINAL_GAP
    return pulses
