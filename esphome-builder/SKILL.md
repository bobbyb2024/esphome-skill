---
name: esphome-builder
description: >-
  Create, clone, edit, validate, compile, deploy, and fleet-manage ESPHome
  devices through a running ESPHome Builder (Dashboard, default port 6052). The
  Builder builds and flashes; this skill makes minimal YAML edits, keeps secrets
  in the Builder, and applies a safe per-class update policy. Use for any
  ESPHome device or fleet task: add/clone/update a node, OTA or serial flash,
  stream logs, classify devices, back up configs, check fleet status. Triggers:
  ESPHome, esphome builder/dashboard, compile/upload/flash esphome, bluetooth
  proxy, sendspin, update my esphome, esphome fleet, home assistant firmware.
license: Apache-2.0
---

# ESPHome Builder

Drive a running **ESPHome Builder** (the ESPHome Dashboard, default
`http://<host>:6052`) to manage one device or a whole fleet. All builds and
deployments happen **inside the Builder**; this skill connects, edits YAML
minimally, keeps credentials in the Builder, and asks the Builder to
build/test/deploy — streaming results back compactly.

The tool is `scripts/esphome_dashboard.py`: one **zero-dependency** Python 3.9+
program (stdlib HTTP + WebSocket). It works under Claude, Codex, or any agent
with a shell.

> **Do not read the script into context — run it.** It is self-documenting:
> `python3 scripts/esphome_dashboard.py <command> --help`. The reference files
> below load only when a task needs them; in most sessions you won't open any.

## Non-negotiable rules

1. **The Builder builds and deploys. Never build or flash locally.** Use
   `validate`/`compile`/`upload`/`run`/`clean`; never call a local
   `esphome`/`platformio`/`esptool`.
2. **Compile before deploy.** Always `validate` → `compile` and only then
   `upload`. Never push firmware you have not successfully compiled.
3. **Secrets stay in the Builder.** Configs reference credentials with
   `!secret <key>` only. Never write a literal Wi-Fi password, API key, OTA
   password, or token into YAML, and never ask the user for secret *values*.
   Read key **names** with `secrets`; verify a config's refs with
   `secrets --check NAME`. The save guard refuses inline secrets unless
   `--allow-secrets` is passed — don't bypass it.
4. **Minimal diffs.** `get` the current YAML, change only what the task needs,
   `put` it back. Don't reorder, reformat, or "tidy" unrelated lines.
5. **Class determines update policy** (see table). Classes come from
   inspection (`classify`), never from the device name. When unsure → `other`.
6. **Back up before modifying; confirm before destructive acts.** `backup`
   before edits/OTA; `delete`/`rename`/factory-reset need explicit user
   confirmation in the same turn. Serial fallback is never automatic.
7. **Single source of truth.** Use https://esphome.io/ as the single source of
   truth for ESPHome documentation (components, options, board IDs, and YAML
   schema) and https://github.com/esphome/esphome as the single source of truth
   for ESPHome code behavior. Issues and PRs may be researched and can inform a
   fix, but default to the official docs and source code over memory, blogs, or
   secondary summaries. See `references/yaml-and-secrets.md`.

## Device classes (update policy)

| Class | What it is | Policy |
| --- | --- | --- |
| `bluetooth-proxy` | Dedicated BLE proxy / tracker, no physical I/O | Actively maintained; config changes allowed |
| `sendspin-player` | Sendspin/Resonate synchronized audio players | Cautious; pin to a user-approved known-good ESPHome version |
| `other` | Everything else (switches, sensors, displays, multi-role) | Best-effort: OTA on the **unchanged** YAML; if it won't compile, skip and report — never force a config change |

Full decision tree, multi-role tiebreaker, canary strategy, and the run-report
format are in `references/fleet-policy.md`.

## First-time setup (once per Builder)

All connection settings live in a `.env` file — nothing is hardcoded. Preferred
path for a Home Assistant add-on install is **HA ingress**: set
`ESPHOME_HA_URL`, `ESPHOME_HA_TOKEN` (a Home Assistant long-lived access token),
and usually leave `ESPHOME_HA_ADDON_SLUG=5c53de3b_esphome`. The tool opens
Home Assistant's `/api/websocket`, calls the `supervisor/api` command
`/ingress/session`, then calls Builder endpoints under
`/api/hassio_ingress/<token>/...` with `Cookie: ingress_session=<session>`.
If ingress setup fails and `ESPHOME_DASHBOARD_URL` is also configured, it falls
back to direct Builder access on port `6052`.

Ask the user for Home Assistant URL/token for ingress and (for fallback) direct
Builder host/IP, port (default `6052`), http vs https, whether direct auth is
required (username/password; not needed for HA add-on ingress or
"leave_front_door_open"), and whether TLS is self-signed. Have the **user** fill
in `.env` (especially tokens/passwords — the agent doesn't need to handle them):

```bash
cp .env.example .env        # edit ESPHOME_HA_* first; optionally ESPHOME_DASHBOARD_* fallback
chmod 600 .env              # and add .env to .gitignore
python3 scripts/esphome_dashboard.py connect      # tries HA ingress, then direct fallback
python3 scripts/esphome_dashboard.py enumerate --save   # cache the live API map
```

Settings resolve in precedence order: CLI flags > real environment > `.env`
(searched as `--env-file` → `$ESPHOME_BUILDER_ENV` → `./.env` →
`~/.config/esphome-builder/.env`). `connect --save` writes the active connection
variables back into `.env` (chmod 600), preserving other lines. Direct Builder
auth is sent as HTTP Basic; HA ingress auth uses the Home Assistant WebSocket
API to create a short-lived ingress session cookie. These Builder credentials
are separate from device `!secret` values, which stay in the Builder's
`secrets.yaml`.

## Orientation (run `--help` for flags)

* **Connection:** `connect`, `enumerate`
* **Inventory / health:** `list`, `status`, `info NAME`, `classify [NAME|--all]`,
  `enumerate`, `secrets`, `downloads NAME`, `serial-ports`
* **Author / change:** `get`, `put`, `create`, `clone`, `rename`, `delete`,
  `lint -f FILE`, `secrets --check NAME`, `diff NAME LOCAL`
* **Validate before save:** `validate -f LOCAL [--name NAME]` — uses beta
  `editor/validate_yaml` to check unsaved content before `put`
* **Build / deploy:** `validate`, `compile`, `upload`, `logs`, `run`, `clean`,
  `update-all` — all accept `--match GLOB` for batches; `compile`/`upload`
  summarize output to a log file by default (use `--verbose` to stream)
* **Operate:** `backup [--out DIR --match GLOB]`, `watch NAME` (online after OTA),
  `serial-ports` (USB flash candidates)
* **Review:** `get --diff LOCAL` — unified diff of Builder vs local config

Add `--json` to any command for machine-parseable output. `logs`/`run` are
bounded by default (`--duration`/`--lines`/`--until`; `--follow` to disable) so
streaming can't hang a turn.

**Beta auto-detection:** `connect` probes the `/ws` endpoint. If available,
`compile`/`validate`/`upload` auto-select the beta API — no `--beta` flag needed.
Pass `--beta` to force beta mode (e.g. on a Builder that exposes both old and
new APIs). `get`/`put` auto-detect SPA HTML responses and fall back to beta
`devices/get_config`/`devices/update_config`.

See `references/workflows.md` for end-to-end recipes (create, modify, clone,
canary fleet update, backup), `references/fleet-policy.md` for the class policy,
and `references/dashboard-api.md` only when debugging or extending the API.

## Builder Beta API (ESPHome Device Builder 2026.6+)

The newer "Device Builder" ESPHome add-on (beta, slug `5c53de3b_esphome-beta`)
uses a different WebSocket protocol and a single `/ws` endpoint. Pass `--beta`
to any build/deploy command to use the beta API:

* **WS endpoint:** `ws://...{ingress}/ws`
* **Format:** `{"command": "<name>", "message_id": "<id>", "args": {<params>}}`
  (NOT the old `{"type": "..."}` format)
* **Read YAML:** `devices/get_config` — result is the raw YAML string
* **Save YAML:** `devices/update_config {configuration, content}` — writes the config
* **Validate:** `devices/validate {configuration}` — streaming events
* **Compile:** `firmware/compile {configuration}` → returns `{job_id}`
* **Follow build:** `firmware/follow_job {job_id}` → streaming line/exit events

The `get`/`put`/`clone`/`backup`/`classify`/`secrets --check` commands
**auto-detect** when the old `/edit` HTTP endpoint returns SPA HTML and
automatically fall back to the beta `devices/get_config` / `devices/update_config`
WebSocket commands. No `--beta` flag is needed for config read/write.

For compile/upload/validate, use `--beta` to force the new API, or rely on auto-
detection for `get`/`put`. The `--beta` flag also switches `list` to use
`devices/list` on the beta `/ws` endpoint. Direct HTTP endpoints that work on
both old and beta Builders (`/devices`, `/json-config`, `/version`, `/ping`) do
not need the `--beta` flag.

Example beta workflow:

```bash
# Connect via HA ingress (auto-detects beta)
python3 scripts/esphome_dashboard.py connect

# Read a config (auto-fallbacks if /edit returns HTML)
python3 scripts/esphome_dashboard.py get kitchen-streamer

# Validate using beta WS API explicitly
python3 scripts/esphome_dashboard.py validate kitchen-streamer --beta

# Compile + follow with beta WS API
python3 scripts/esphome_dashboard.py compile kitchen-streamer --beta

# Or just use upload which also compiles via beta
python3 scripts/esphome_dashboard.py upload kitchen-streamer --beta
```
