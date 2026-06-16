# Fleet policy

How to manage a fleet of ESPHome devices safely. The governing principle:
**keep the fleet healthy without ever breaking a working device.** Prefer
agent-reasoned, per-device decisions over blind batch runs — the tool gives you
building blocks (`classify`, `status`, `backup`, `validate`, `compile`,
`upload`, `watch`); you apply the policy below per device.

## Golden rules

1. Compile → validate → only then install. Never push uncompiled firmware.
2. Never force an update that requires a config change unless the device is in
   an actively-managed class (`bluetooth-proxy` or `sendspin-player`).
   "Requires a config change" = the current YAML no longer compiles against the
   current ESPHome version and the fix needs a YAML edit.
3. Class is determined by inspection (`classify`), not by device name.
4. Back up before any YAML write or OTA (`backup`).
5. Confirm before destructive acts (delete, rename, factory reset) in the same
   turn.
6. Serial fallback is never automatic — report an OTA failure; don't tell the
   user to plug in USB unless they ask.

## Classification (first match wins)

Run `classify NAME` / `classify --all` (add `--deep` to expand `packages:`/
`!include` via `/json-config`). Decision order:

1. **Sendspin / Resonate present → `sendspin-player`.** Any of: a `sendspin`/
   `sendspin_audio_player`/`sendspin_player` component, legacy `resonate*`, a
   `media_player:` with `platform: sendspin|resonate`, or a known Sendspin
   package (e.g. `github://RASPIAUDIO/esphomeLuxeMusic`). This wins even if the
   device also has `bluetooth_proxy:`.
2. **Bluetooth proxy as primary role → `bluetooth-proxy`.** Has
   `bluetooth_proxy:` or `esp32_ble_tracker:` **and no real hardware**
   (no `switch`/`light`/`cover`/`climate`/`fan`/`lock`/`display`/`output`/etc.).
   Diagnostic `sensor`/`binary_sensor` (wifi_signal, uptime) do not disqualify.
3. **Multi-role tiebreaker → `other`.** Has a proxy/tracker **and** real
   hardware. The proxy is secondary; primary purpose is the hardware. Don't
   YAML-edit it just to update the proxy role.
4. **Otherwise → `other`.** Switches, relays, plugs, climate, displays, sensors,
   lights/LEDs, voice devices (unless also Sendspin — Sendspin wins), one-offs.

When genuinely ambiguous, classify `other` and note it. Conservative wins; the
user can override.

## Per-class update procedure

For every device, record path, name, board/MCU, top-level components, packages,
assigned class, and a one-line rationale (a JSON array is ideal — `classify
--all --json` produces most of this).

**`bluetooth-proxy`** — actively maintained.
`backup` → `validate` → `compile`. If compile fails on a breaking change, make
the **minimal** YAML fix (per esphome.io migration notes), re-`validate`/
`compile`, then `upload` and `watch` for it to return online.

**`sendspin-player`** — actively maintained but cautious. Sendspin is
experimental and tracks WIP branches. Only update when the user has approved a
known-good ESPHome version; pin to it (persist the pinned version, e.g. in agent
memory). Otherwise leave as-is. Same backup → validate → compile → upload →
watch flow once a version is approved.

**`other`** — best-effort, zero-YAML-edit.
`backup` → `compile` the **unchanged** YAML against the current version.
* Compiles → `upload`, `watch`. 
* Fails → **skip**. Do not edit the YAML. Record a skip code and move on.

## Canary-first rollout

When updating many same-class devices, update **one canary first**, confirm it
compiles, uploads, and comes back online (`watch`), and ideally that it behaves
(brief `logs --duration 30`). Only then proceed to the rest (`--match GLOB`).
Stop the batch on the first failure and report.

## No automatic rollback

ESPHome has no in-place rollback: reverting means recompiling and re-OTAing the
previously-known-good YAML. So **backups are the rollback plan** — keep the
pre-change YAML (`backup`) and, if a device misbehaves after an update, restore
that file (`put`) and re-`compile`/`upload`. Never auto-rollback silently;
surface the failure and the restore step to the user.

## Skip / status codes for reporting

Account for **every** device in a run. Suggested codes:

* `updated` — compiled and OTA'd to the current version.
* `up-to-date` — already on the target version; nothing to do.
* `skipped-other-breaking` — `other` device whose unchanged YAML no longer
  compiles; left alone by policy.
* `skipped-sendspin-unpinned` — `sendspin-player` with no approved version.
* `offline` — device not reachable for OTA (report; don't serial-fallback).
* `compile-failed` — actively-managed device that failed compile after a fix
  attempt; needs human attention.
* `needs-confirmation` — change is destructive or requires user sign-off.

Fill in `assets/run-report.template.md` so the user sees one row per device with
its class, action, code, and (on failure) the log path.

## Cautions

* Don't edit a YAML on the dashboard mid-build — the Builder caches the parsed
  config at compile start, so edits during a build cause confusing errors.
* Optional: if the user has Home Assistant configured (`HA_URL`/`HA_TOKEN`), you
  can cross-check device availability there, but the Builder's `status`
  (and `/events` via `watch`) already report online state — HA is not required.
