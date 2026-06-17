# YAML, secrets, and the esphome.io doc map

## Secrets policy (hard requirement)

Device YAML must never contain literal credentials. Use `!secret <key>` and
keep the values in the Builder's `secrets.yaml`. The model must not see or store
secret values; only **key names** are ever read (`secrets` command → the
`/secret_keys` endpoint, names only).

`secrets.yaml` is a **flat mapping of key → scalar** living in the same config
directory as the device files. It must not be committed to version control.

ESPHome security guidance (https://esphome.io/guides/security_best_practices/):

* **Wi-Fi credentials may be shared** across devices.
* **API encryption keys, OTA passwords, and web-server credentials must be
  UNIQUE per device.** Never reuse them.

Recommended key naming so per-device uniqueness is obvious:

```yaml
# secrets.yaml (managed in the Builder, NOT by the model)
wifi_ssid: "..."
wifi_password: "..."
api_key_living_room: "..."        # 32-byte base64, unique per device
ota_password_living_room: "..."   # unique per device
```

```yaml
# living-room.yaml
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
api:
  encryption:
    key: !secret api_key_living_room
ota:
  - platform: esphome
    password: !secret ota_password_living_room
```

When a config needs a secret that doesn't exist yet, tell the user the exact
key name to add in the Builder and what kind of value it expects — do not invent
or write the value. To generate a fresh API key value, the user can run
`esphome wizard` or any 32-byte base64 generator on their own machine; the model
should not handle the value.

The `lint` command (and the `put`/`create`/`clone` guard) flags any sensitive
key with a literal value. If it fires, fix the YAML to use `!secret`; do not
bypass with `--allow-secrets` unless the user explicitly insists and understands
the value will be stored in the config.

Before compiling a new or cloned device, run `secrets --check NAME` (or
`--file PATH` for a local YAML): it lists the `!secret` keys the config
references and flags any that are missing from the Builder's `secrets.yaml`, so
a compile won't fail on an undefined secret. It compares names only — values are
never read.

## Minimal-diff editing

* Always `get` the current YAML before editing; never regenerate from scratch.
* Change only the lines the task requires. Preserve indentation, key order,
  comments, blank lines, and quoting style.
* Prefer `substitutions:` and packages for repeated values rather than editing
  many lines.
* After editing, `lint`, then `validate`. Show the user the diff if the change
  is non-trivial.

## ESPHome docs and code source of truth

Use https://esphome.io/ as the single source of truth for ESPHome documentation:
component names, options, platforms, board IDs, YAML schema, and migration notes.
Use https://github.com/esphome/esphome as the single source of truth for ESPHome
code behavior when docs are incomplete. Issues and PRs may be researched and can
inform a fix, but the default is always official docs and source code over
memory, blogs, snippets, or secondary summaries.

Key documentation entry points:

* Index of all components: https://esphome.io/index.html
* Core config & `esphome:` block: https://esphome.io/components/esphome.html
* YAML features (`!secret`, `!include`, packages): https://esphome.io/guides/yaml.html
* Substitutions: https://esphome.io/guides/substitutions.html
* Configuration types / units: https://esphome.io/guides/configuration-types.html
* Wi-Fi: https://esphome.io/components/wifi.html
* Native API (+ encryption): https://esphome.io/components/api.html
* OTA: https://esphome.io/components/ota/
* Logger: https://esphome.io/components/logger.html
* Web server: https://esphome.io/components/web_server.html
* Bluetooth proxy: https://esphome.io/components/bluetooth_proxy.html
* CLI reference: https://esphome.io/guides/cli.html
* Security best practices: https://esphome.io/guides/security_best_practices/
* FAQ (troubleshooting): https://esphome.io/guides/faq.html
* ESPHome source repository: https://github.com/esphome/esphome

Per-platform board IDs (ESP32/ESP8266/RP2040/etc.) are linked from each
platform's page under https://esphome.io/components/esp32.html and friends; the
Builder's `/boards` endpoint also lists them for the installed version.

When unsure whether a component or option exists in the user's ESPHome version,
check `enumerate`'s reported version against the docs, or just `validate` — the
Builder is the final authority on what compiles.
