# ac-remote

Control A/C units over IR through BroadLink RM4 mini blasters, from a small Docker app running on an
always-on machine in the same LAN.

```
browser ──► TLS proxy ──┐
                        ├──► ac-remote (Docker, LAN server, 127.0.0.1) ──► RM4 mini(s) ──IR──► A/C units
chat bot ──SSH──► python -m app.cli ┘
```

## Supported protocols

| Protocol | Units | Verified on hardware (2026-10-15) |
|---|---|---|
| `mitsubishi112` | Reetech (Mitsubishi Electric 112-bit) | off (learned code); on cool 20°C / 25°C fan med (generated) |
| `coolix` | Midea (Coolix 24-bit) | off; on cool 20°C / 22°C / 25°C fan auto (generated) |

Codes are generated from the protocol layout (IRremoteESP8266), so every temperature/mode/fan
combination is available without learning each button. Settings other than those above are not
yet verified on the real units. IR is one-way: the app shows the last *commanded* state, not the
real one.

## Several units, several RM4 minis

Each unit in `config/units.yaml` is sent through `rm4.host` unless it sets its own `rm4_host`.
Two units sharing a protocol (e.g. two Midea) react to the same code, so they can only be controlled
separately with one RM4 mini per unit, each placed so that the other unit cannot see its IR. After
placing them, check both directions: switch one unit while the other is in a different state and
make sure only the target reacts. Moving an RM4 later can break that separation.

## RM4 mini prerequisites

- Joined to Wi-Fi with the BroadLink app, **Lock device = off** (a locked device refuses local control).
- Fixed IP via a DHCP reservation for its MAC.
- Do not update the firmware (tested on firmware 62092, devtype `0x520c`).
- Find devices on the LAN: `python -c "import broadlink; [print(d.host[0], d.model) for d in broadlink.discover(timeout=5)]"`.

## Run

On an always-on machine in the same LAN as the RM4 minis:

```bash
cp .env.example .env                       # set APP_PASSWORD (>= 12 chars), BIND_ADDR, PORT
cp config/units.example.yaml config/units.yaml   # adjust; must be readable by uid 10001 (e.g. mode 0644)
docker compose up -d --build
docker compose exec ac-remote python -c "import broadlink; d=broadlink.hello('192.168.5.48'); d.auth(); print('ok')"
```

`config/units.yaml` is read at startup: after editing it, run `docker compose restart ac-remote`.
State and logs live in the named volumes `ac-data` / `ac-logs` (owned by the container's uid 10001).

## Access

- **Web UI:** `http://<BIND_ADDR>:<PORT>/` (HTTP Basic auth). Keep `BIND_ADDR=127.0.0.1` and publish it
  through a TLS reverse proxy, or bind it to a VPN-only address — never expose plain HTTP to the internet.
  The app does not throttle failed logins: behind a public reverse proxy, rate-limit `401`s there
  (e.g. nginx `limit_req`, Caddy `rate_limit`) or put fail2ban on the proxy log.
- **CLI (chat bots, scripts):** runs inside the container and takes the credentials from the container
  env, so the caller never holds the password:

  ```bash
  docker compose exec -T ac-remote python -m app.cli list
  docker compose exec -T ac-remote python -m app.cli midea on --mode cool --temp 22 --fan auto
  docker compose exec -T ac-remote python -m app.cli midea off
  ```

  Options left out reuse the unit's last commanded state. Exit code 0 = sent, 1 = error (message on
  stderr), 2 = bad arguments. A remote caller (e.g. a chat bot on a VPS) can run it over SSH:
  `ssh <lan-server> 'cd ~/ac-remote && docker compose exec -T ac-remote python -m app.cli midea off'`.
  Validate every argument on the caller side before building that command line.
- **API:** `GET /api/units`, `POST /api/units/{id}` with `{"power": true, "mode": "cool", "temp": 25, "fan": "auto"}`,
  `GET /healthz` (no auth).

Logs: `docker compose logs ac-remote`, or `docker compose exec ac-remote tail -f /app/logs/ac-remote.log`.

## Dependencies

Pinned: `requirements.txt` (direct) + `constraints.txt` (full frozen set), base image by tag + digest in
`Dockerfile`. Upgrade deliberately: bump, run the tests, rebuild, then re-freeze `constraints.txt`.
Pinning also freezes out security patches, so re-pin regularly: `docker pull python:3.12-slim`, take the
new digest (`docker image inspect --format '{{index .RepoDigests 0}}'`), refresh `constraints.txt`, run the
tests, redeploy. The digest wins over the tag in `FROM`, so the tag is only a label — always re-pin the
digest on purpose.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # fake RM4, no hardware needed
```
