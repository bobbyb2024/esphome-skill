# ESPHome fleet run — {DATE}

**Builder:** {BUILDER_URL}  
**ESPHome version (Builder):** {VERSION}  
**Scope:** {all devices | --match GLOB | named list}  
**Operator:** {user/agent}

## Summary

- Devices considered: {N}
- Updated: {n}  ·  Up-to-date: {n}  ·  Skipped: {n}  ·  Failed: {n}  ·  Needs confirmation: {n}

## Per-device results

| Device (config) | Class | Action | Code | Notes / log |
| --- | --- | --- | --- | --- |
| living-room.yaml | other | compile (unchanged) | up-to-date | — |
| bedroom-ble.yaml | bluetooth-proxy | compile+OTA | updated | online confirmed |
| kitchen-speaker.yaml | sendspin-player | — | skipped-sendspin-unpinned | awaiting approved version |
| garage.yaml | other | compile (unchanged) | skipped-other-breaking | log: ~/.cache/esphome-builder/logs/garage-compile-*.log |

## Follow-ups requiring the user

- {e.g. approve a known-good Sendspin ESPHome version to pin}
- {e.g. garage.yaml needs a YAML migration — out of policy for `other`; confirm before editing}
- {e.g. add missing secret keys: api_key_garage_2, ota_password_garage_2}

## Rollback notes

Pre-change YAMLs backed up to: {BACKUP_DIR}. To revert a device: `put NAME -f
{BACKUP_DIR}/NAME.yaml` then `compile` + `upload`.
