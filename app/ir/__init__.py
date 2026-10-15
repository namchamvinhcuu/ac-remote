"""Build BroadLink IR packets for supported A/C protocols."""

from broadlink.remote import pulses_to_data

from app.ir import coolix, mitsubishi112

PROTOCOLS = ("mitsubishi112", "coolix")


def build_packet(protocol: str, power: bool, mode: str, temp: int, fan: str) -> bytes:
    """Return a BroadLink `send_data` packet for one A/C command."""
    if protocol == "mitsubishi112":
        pulses = mitsubishi112.to_pulses(mitsubishi112.build_state(power, mode, temp, fan))
    elif protocol == "coolix":
        pulses = coolix.to_pulses(coolix.build_code(power, mode, temp, fan))
    else:
        raise ValueError(f"unknown protocol {protocol!r}; expected one of {PROTOCOLS}")
    return bytes(pulses_to_data(pulses))
