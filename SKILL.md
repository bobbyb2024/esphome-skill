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
7. **esphome.io is the source of truth** for components, options, and board
   IDs — not memory. See `references/yaml-and-secrets.md`.

## Device classes (update policy)

| Class | What it is | Policy |
| --- | --- | --- |
| `bluetooth-proxy` | Dedicated BLE proxy / tracker, no physical I/O | Actively maintained; config changes allowed |
| `sendspin-player` | Sendspin/Resonate synchronized audio players | Cautious; pin to a user-approved known-good ESPHome version |
| `other` | Everything else (switches, sensors, displays, multi-role) | Best-effort: OTA on the **unchanged** YAML; if it won't compile, skip and report — never force a config change |

Full decision tree, multi-role tiebreaker, canary strategy, and the run-report
format are in `references/fleet-policy.md`.

## First-time setup (once per Builder)

All connection settings live in a `.env` file — nothing is hardcoded. Ask the
user for host/IP, port (default `6052`), http vs https, whether auth is required
(username/password; not needed for HA add-on ingress or "leave_front_door_open"),
and whether TLS is self-signed. Have the **user** fill in `.env` (especially the
password — the agent doesn't need to handle it):

```bash
cp .env.example .env        # then edit: ESPHOME_DASHBOARD_URL, _USERNAME, _PASSWORD, _INSECURE
chmod 600 .env              # and add .env to .gitignore
python3 scripts/esphome_dashboard.py connect      # reads .env, verifies auth
python3 scripts/esphome_dashboard.py enumerate --save   # cache the live API map
```

Settings resolve in precedence order: CLI flags > real environment > `.env`
(searched as `--env-file` → `$ESPHOME_BUILDER_ENV` → `./.env` →
`~/.config/esphome-builder/.env`). `connect --save` writes the variables back
into `.env` (chmod 600), preserving other lines. Auth is sent as HTTP Basic on
every request and the WS handshake. These Builder credentials are separate from
device `!secret` values, which stay in the Builder's `secrets.yaml`.

## Orientation (run `--help` for flags)

* **Inventory / health:** `list`, `status`, `info NAME`, `classify [NAME|--all]`,
  `enumerate`, `secrets`, `downloads NAME`
* **Author / change:** `get`, `put`, `create`, `clone`, `rename`, `delete`,
  `lint -f FILE`, `secrets --check NAME`
* **Build / deploy:** `validate`, `compile`, `upload`, `logs`, `run`, `clean`,
  `update-all` — all accept `--match GLOB` for batches; `compile`/`upload`
  summarize output to a log file by default (use `--verbose` to stream)
* **Operate:** `backup [--out DIR --match GLOB]`, `watch NAME` (online after OTA)

Add `--json` to any command for machine-parseable output. `logs`/`run` are
bounded by default (`--duration`/`--lines`/`--until`; `--follow` to disable) so
streaming can't hang a turn.

See `references/workflows.md` for end-to-end recipes (create, modify, clone,
canary fleet update, backup), `references/fleet-policy.md` for the class policy,
and `references/dashboard-api.md` only when debugging or extending the API.
