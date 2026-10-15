# ac-remote

Control A/C units over IR through a BroadLink RM4 mini, from a Docker app running on a VPS.

```
browser ──► ac-remote (Docker, VPS) ──Tailscale──► LAN subnet router ──► RM4 mini 192.168.5.48 ──IR──► A/C
```

## Supported protocols

| Protocol | Units | Verified on hardware (2026-10-15) |
|---|---|---|
| `mitsubishi112` | Reetech (Mitsubishi Electric 112-bit) | off (learned code), on cool 25°C fan med (generated) |
| `coolix` | Midea (Coolix 24-bit) | off, on cool 25°C fan auto (generated) |

Codes are generated from the protocol layout (IRremoteESP8266), so every temperature/mode/fan
combination is available without learning each button. Other settings than those above are not
yet verified on the real units. IR is one-way: the app shows the last *commanded* state, not the
real one.

Two units sharing a protocol (both Midea) react to the same code. To control them separately, put
a second RM4 mini near one of them and set `rm4_host` for that unit.

## RM4 mini prerequisites

- Joined to Wi-Fi with the BroadLink app, **Lock device = off** (a locked device refuses local control).
- Fixed IP via a DHCP reservation for its MAC.
- Do not update the firmware (tested on firmware 62092, devtype `0x520c`).

## Network: VPS → LAN over Tailscale

1. On an always-on LAN machine: `sudo tailscale up --advertise-routes=192.168.5.0/24`
   (enable IP forwarding first), then approve the route in the Tailscale admin console.
2. On the VPS: `sudo tailscale up --accept-routes`.
3. Check from the VPS: `python3 -c "import broadlink; d=broadlink.hello('192.168.5.48'); d.auth(); print('ok')"`.

## Run

```bash
cp .env.example .env                       # set APP_PASSWORD (>= 12 chars), BIND_ADDR
cp config/units.example.yaml config/units.yaml   # must be readable by uid 10001 (e.g. mode 0644)
docker compose up -d --build
docker compose exec ac-remote python -c "import broadlink; d=broadlink.hello('192.168.5.48'); d.auth(); print('ok')"
```

State and logs live in the named volumes `ac-data` / `ac-logs` (owned by the container's uid 10001).

UI: `http://<BIND_ADDR>:<PORT>/` (HTTP Basic auth). Keep it on `127.0.0.1` behind a TLS reverse
proxy, or bind it to the VPS Tailscale IP — never expose plain HTTP to the internet. The app does not
throttle failed logins: when it sits behind a public reverse proxy, rate-limit `401`s there
(e.g. nginx `limit_req`, Caddy `rate_limit`) or put fail2ban on the proxy log.

API: `GET /api/units`, `POST /api/units/{id}` with `{"power": true, "mode": "cool", "temp": 25, "fan": "auto"}`,
`GET /healthz`. Logs: `docker compose logs ac-remote`, or `docker compose exec ac-remote tail -f /app/logs/ac-remote.log`.

Dependencies are pinned: `requirements.txt` (direct) + `constraints.txt` (full frozen set), base image by
tag + digest in `Dockerfile`. Upgrade deliberately: bump, run the tests, rebuild, then re-freeze `constraints.txt`.
Pinning also freezes out security patches, so do this at least **monthly**: `docker pull python:3.12-slim`, take the
new digest (`docker image inspect --format '{{index .RepoDigests 0}}'`), refresh `constraints.txt`, run the tests, redeploy.
The digest wins over the tag in `FROM`, so the tag is only a label — always re-pin the digest on purpose.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
