# ESPHome Builder API reference

The ESPHome Builder (Dashboard) is a Tornado web app, usually on port `6052`.
It exposes a small HTTP API plus several **WebSocket command** endpoints that
spawn the `esphome` CLI on the Builder and stream its output. Paths vary
slightly across versions, so `scripts/esphome_dashboard.py enumerate` probes the
live instance. For API behavior beyond the live probe, use
https://github.com/esphome/esphome as the source-code truth; Issues and PRs may
inform a fix but do not outrank official source.

> You normally don't need this file: the tool wraps every endpoint below. Read
> it only to debug an odd Builder version or extend the tool. Build/log output
> from the WebSocket commands is summarized client-side by the tool (full log to
> a file, compact result to stdout) — that summarization is not part of the API.

## Authentication

`is_authenticated()` accepts any of:

* **HTTP Basic** — `Authorization: Basic base64(user:pass)`. Works on plain
  HTTP requests **and** on the WebSocket handshake. This is what the tool uses.
* **Session cookie** — `authenticated=yes`, obtained by POSTing to `/login`.
* **HA add-on ingress** — header `X-HA-Ingress: YES` (set by the add-on's
  nginx). With the add-on's "leave_front_door_open" option, auth is disabled
  entirely and no credentials are needed.

When auth is off, every endpoint is open. A `401` or a `3xx` redirect whose
`Location` contains `login` means credentials are required or wrong. The tool
reads these credentials from variables (`ESPHOME_DASHBOARD_USERNAME` /
`_PASSWORD`), sourced from the real environment or a `.env` file — never
hardcoded.

Behind a reverse proxy/custom domain, set `ESPHOME_TRUSTED_DOMAINS` (comma-
separated hostnames) in the Builder's environment so WebSocket origin checks
pass. The tool sends a same-origin `Origin` header to satisfy the default check.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/version` | ESPHome version string |
| GET | `/devices` | JSON list of configured + discovered devices (has `deployed_version`, `target_platform`, `loaded_integrations`) |
| GET | `/ping` | online/offline map `{filename: bool}` (when present); powers `status` |
| GET | `/info?configuration=NAME.yaml` | Stored metadata for one device |
| GET | `/edit?configuration=NAME.yaml` | **Raw YAML** of a config |
| POST | `/edit?configuration=NAME.yaml` | Save raw YAML (body = file text); creates the file if absent |
| POST | `/delete?configuration=NAME.yaml` | Move config to trash |
| POST | `/undo-delete?configuration=NAME.yaml` | Restore from trash |
| POST | `/wizard` | Create a config (JSON body, see below) |
| POST | `/import` | Adopt a discovered/importable device |
| POST | `/ignore-device` | `{ "name": ..., "ignore": true|false }` |
| GET | `/downloads?configuration=NAME.yaml` | Downloadable binary variants |
| GET | `/download.bin?configuration=NAME.yaml&file=...&type=...` | Fetch a compiled binary |
| GET | `/secret_keys` | **Key names only** from `secrets.yaml` (never values). Some builds use `/secret-keys`. |
| GET | `/serial-ports` | Local serial ports + the `OTA` pseudo-port |
| GET | `/json-config?configuration=NAME.yaml` | Validated config as JSON (expands `packages:`/`!include`; used by `classify --deep`) |
| GET | `/boards` | Boards for a platform |
| GET/POST | `/login`, `/logout` | Cookie session auth |
| GET | `/prometheus-sd` | Prometheus service discovery |

The `/devices` response (shape, abbreviated):

```json
{
  "configured": [
    {"name": "living-room", "friendly_name": "Living Room",
     "configuration": "living-room.yaml", "address": "living-room.local",
     "web_port": 80, "target_platform": "ESP32",
     "loaded_integrations": ["api", "wifi", "..."], "deployed_version": "..."}
  ],
  "importable": [ ... ]
}
```

### `/wizard` body

```json
{ "name": "Friendly Name", "type": "basic",
  "platform": "ESP32", "board": "esp32dev",
  "ssid": "...", "psk": "...", "password": "..." }
```

* `type: "basic"` auto-generates an OTA password and an API encryption key and
  scaffolds Wi-Fi; `type: "empty"` makes a bare file; `type: "upload"` takes a
  base64 `file_content`.
* `name` becomes `friendly_name`; the file/node name is the slug of it.
* Returns `{"configuration": "<slug>.yaml"}`; `409` if the file already exists.

> The wizard is the official creation path, but `basic` may write a generated
> key/password into the YAML. For the "no secrets in YAML" guarantee, create
> with `create --from-file` using `!secret` references instead.

## WebSocket command endpoints

Each is a WebSocket URL. Open it (with the Basic auth header if needed), then
send a JSON **spawn** message; the server streams JSON events and closes.

```
client -> {"type": "spawn", "configuration": "NAME.yaml", ...}
client -> {"type": "stdin",  "data": "...\n"}        # optional, to feed prompts
server -> {"event": "line",  "data": "build output...\n"}   # repeated
server -> {"event": "exit",  "code": 0}                     # then socket closes
```

| WS path | spawn fields | runs |
| --- | --- | --- |
| `/validate` | `configuration` | `esphome config NAME.yaml` |
| `/compile` | `configuration` [, `only_generate`] | `esphome compile NAME.yaml` |
| `/upload` | `configuration`, `port` | `esphome upload NAME.yaml --device PORT` |
| `/run` | `configuration`, `port` | `esphome run NAME.yaml --device PORT` |
| `/logs` | `configuration`, `port` | `esphome logs NAME.yaml --device PORT` |
| `/clean` | `configuration` | `esphome clean NAME.yaml` |
| `/clean-mqtt` | `configuration` | `esphome clean-mqtt NAME.yaml` |
| `/clean-all` | — | clean all build dirs |
| `/rename` | `configuration`, `newName` | `esphome rename ...` |
| `/update-all` | — | update all devices |
| `/vscode`, `/ace` | — | editor backends |

`port` is the literal string `OTA` for over-the-air, or a serial device path
(e.g. `/dev/ttyUSB0`) from `/serial-ports`. For OTA the Builder resolves the
device address (mDNS/DNS) and uses the ESPHome OTA v2 protocol.

## Real-time events: `/events`

`DashboardEventsWebSocket` pushes live device online/offline and add/remove/
update events. On connect it sends an `initial_state` with the device list and
ping map; thereafter `entry_state_changed`, `entry_added`, `entry_removed`,
`entry_updated`, and importable-device events. Send `{"event":"ping"}` to get a
`pong`; send `{"event":"refresh"}` to force an immediate poll. Useful for
watching a node come back online after an OTA, but not required for CRUD.
