# Workflows

Copy-pasteable recipes. Assume `connect --save` ran, so no `--url`/credential
flags are needed. Stop and fix on any non-zero exit / `FAILED`; surface the
Builder's errors verbatim. `compile`/`upload` print a compact result and write
the full log to a file (path shown) — read that file only if you need detail.

## Connect, map, inventory

```bash
cp .env.example .env        # user fills ESPHOME_HA_* for ingress; optional ESPHOME_DASHBOARD_* fallback
chmod 600 .env              # and gitignore it
python3 scripts/esphome_dashboard.py connect            # HA ingress first, direct 6052 fallback next
python3 scripts/esphome_dashboard.py enumerate --save   # cache API surface
python3 scripts/esphome_dashboard.py status             # online + update-available
python3 scripts/esphome_dashboard.py classify --all     # device classes
```

Settings come from variables (CLI flags > real env > `.env`); no host, token, or
password is ever hardcoded. Use `--env-file PATH` to point at a specific `.env`.
For HA add-on ingress, set `ESPHOME_HA_URL`, `ESPHOME_HA_TOKEN`, and usually keep
`ESPHOME_HA_ADDON_SLUG=5c53de3b_esphome`; the tool creates the ingress session
through Home Assistant and falls back to `ESPHOME_DASHBOARD_URL` if ingress fails.
Use https://esphome.io/ as the default source for ESPHome docs and
https://github.com/esphome/esphome for source-code behavior; Issues and PRs may
inform fixes, but do not outrank official docs/source.

## Create a device (recommended: minimal file with !secret)

```bash
python3 scripts/esphome_dashboard.py secrets               # what keys exist?
cat > /tmp/garage.yaml <<'YAML'
esphome:
  name: garage
  friendly_name: Garage
esp32:
  board: esp32dev
  framework: { type: esp-idf }
logger:
api:
  encryption:
    key: !secret api_key_garage
ota:
  - platform: esphome
    password: !secret ota_password_garage
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
YAML
python3 scripts/esphome_dashboard.py lint -f /tmp/garage.yaml
python3 scripts/esphome_dashboard.py create garage --from-file /tmp/garage.yaml
python3 scripts/esphome_dashboard.py secrets --check garage   # any missing keys?
python3 scripts/esphome_dashboard.py validate garage
python3 scripts/esphome_dashboard.py compile  garage
python3 scripts/esphome_dashboard.py upload   garage          # OTA default
```

If `secrets --check` reports missing keys, have the user add them to the
Builder's `secrets.yaml` (unique per device for api/ota) before compiling.

## Modify a device (minimal diff)

```bash
python3 scripts/esphome_dashboard.py backup --match garage     # snapshot first
python3 scripts/esphome_dashboard.py get garage -o /tmp/garage.yaml
# ...smallest necessary edit...
python3 scripts/esphome_dashboard.py lint -f /tmp/garage.yaml
python3 scripts/esphome_dashboard.py put  garage -f /tmp/garage.yaml
python3 scripts/esphome_dashboard.py validate garage && \
python3 scripts/esphome_dashboard.py compile garage && \
python3 scripts/esphome_dashboard.py upload  garage
python3 scripts/esphome_dashboard.py watch  garage             # back online?
```

## Clone a device

```bash
python3 scripts/esphome_dashboard.py clone garage garage-2 --friendly-name "Garage 2"
# Only identity lines change; comments and !secret refs stay byte-identical.
# Give the clone its OWN unique secrets, then deploy:
#   add api_key_garage_2 / ota_password_garage_2 to the Builder's secrets.yaml
#   point garage-2.yaml at them (minimal edit), then:
python3 scripts/esphome_dashboard.py secrets --check garage-2
python3 scripts/esphome_dashboard.py validate garage-2 && \
python3 scripts/esphome_dashboard.py compile garage-2 && \
python3 scripts/esphome_dashboard.py upload  garage-2
```

## Diagnose a device

```bash
python3 scripts/esphome_dashboard.py logs garage --duration 30      # bounded sample
python3 scripts/esphome_dashboard.py logs garage --until 'WiFi.*connected'
python3 scripts/esphome_dashboard.py logs garage --port /dev/ttyUSB0 --lines 100
```

## Fleet update run (policy-driven, canary-first)

Per `references/fleet-policy.md`. Don't blind-batch; reason per device.

```bash
# 1. Inventory + classify; back up everything before touching anything.
python3 scripts/esphome_dashboard.py classify --all --json > /tmp/classes.json
python3 scripts/esphome_dashboard.py backup --out ./esphome-backup

# 2. bluetooth-proxy: canary one, confirm, then the rest.
python3 scripts/esphome_dashboard.py compile bedroom-ble && \
python3 scripts/esphome_dashboard.py upload  bedroom-ble && \
python3 scripts/esphome_dashboard.py watch   bedroom-ble
# canary good -> batch the class:
python3 scripts/esphome_dashboard.py compile --match '*-ble*'
python3 scripts/esphome_dashboard.py upload  --match '*-ble*'

# 3. other: compile the UNCHANGED yaml; OTA only if it compiles, else skip.
python3 scripts/esphome_dashboard.py compile desk-plug \
  && python3 scripts/esphome_dashboard.py upload desk-plug \
  || echo "skip desk-plug: skipped-other-breaking (left unchanged)"

# 4. sendspin-player: only if a known-good version is approved+pinned; else skip.

# 5. Write the report from assets/run-report.template.md (one row per device).
```

`update-all` (the Builder's own "update everything to current") exists but
ignores class policy — prefer the per-class flow above unless the user explicitly
wants a blanket update. It is guarded and requires:

```bash
python3 scripts/esphome_dashboard.py update-all --confirm update-all
```

## Destructive operations

```bash
python3 scripts/esphome_dashboard.py rename old-name new-name --confirm old-name
python3 scripts/esphome_dashboard.py delete old-name --confirm old-name
```

The confirmation must match the current config name (with or without `.yaml`).

## Backup for git

```bash
python3 scripts/esphome_dashboard.py backup --out ~/esphome-configs
# writes every device YAML + MANIFEST.json. Check MANIFEST.json for
# inline_secret_findings before committing backups; keep secrets.yaml out of git.
```

## Troubleshooting

* **HA ingress 401 / `No valid ingress session`** → the ingress cookie is missing
  or expired. Re-run `connect`; it should create a fresh session through
  Home Assistant `/api/websocket` (`supervisor/api` endpoint `/ingress/session`).
  If that fails, check `ESPHOME_HA_URL`, admin-capable `ESPHOME_HA_TOKEN`, and
  `ESPHOME_HA_ADDON_SLUG`; the tool will fall back to `ESPHOME_DASHBOARD_URL` if set.
* **401 / login redirect on direct port** → wrong/missing direct Builder creds; re-run `connect`.
* **TLS error** → self-signed cert; add `--insecure` (or set
  `ESPHOME_DASHBOARD_INSECURE=1`).
* **WebSocket handshake failed** behind a proxy → set `ESPHOME_TRUSTED_DOMAINS`
  on the Builder, or hit it directly by IP:port.
* **compile fails on a component/option** → check that component on
  https://esphome.io/ for the Builder's version (`enumerate` reports it), and
  inspect https://github.com/esphome/esphome when docs are insufficient; Issues
  and PRs can inform a fix but do not outrank official docs/code.
* **OTA can't find the device** → `status`/`watch` to confirm online; mDNS may
  be down across VLANs; flash once over serial to seed Wi-Fi.
* **secret not found at compile** → `secrets --check NAME`; have the user add the
  missing key (never inline the value).
* **backup reports inline secret findings** → do not commit that backup until the
  YAML is migrated to `!secret` references or the user explicitly accepts the risk.
