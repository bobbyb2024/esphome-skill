# ESPHome Builder Skill

A zero-dependency agent skill for managing ESPHome devices through a running
ESPHome Builder/Dashboard. The Builder performs validation, compilation, upload,
and logs; the skill talks to the Builder API, keeps YAML edits minimal, and
preserves `!secret` references.

## Layout

```text
esphome-builder/                  # installable skill directory
  SKILL.md                        # lean always-on entrypoint
  .env.example                    # placeholder-only Builder connection config
  scripts/esphome_dashboard.py    # stdlib-only CLI client
  references/                     # load-on-demand docs for progressive disclosure
  assets/                         # report templates

tests/                            # offline regression tests
tools/package_skill.py            # builds dist/esphome-builder-skill.tar.gz
```

The repo intentionally keeps **one canonical source tree**: `esphome-builder/`.
Generated archives belong in `dist/` and are not committed.

## Install/use from a checkout

```bash
cd esphome-builder
cp .env.example .env
chmod 600 .env
# Fill in ESPHOME_DASHBOARD_URL and optional Builder auth yourself.
python3 scripts/esphome_dashboard.py connect
python3 scripts/esphome_dashboard.py enumerate --save
python3 scripts/esphome_dashboard.py status
python3 scripts/esphome_dashboard.py classify --all
```

## Install as an agent skill

Copy or extract `esphome-builder/` into the target agent's skills directory. For
example:

```bash
python3 tools/package_skill.py
tar -xzf dist/esphome-builder-skill.tar.gz -C ~/.claude/skills/
```

## Test

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 tools/package_skill.py --check
```

## Package

```bash
python3 tools/package_skill.py
# writes dist/esphome-builder-skill.tar.gz
```

The package helper creates reproducible archives, includes the repository
`LICENSE`, and refuses to include `.env`, bytecode, logs, backups, capability
caches, or generated tarballs.

## Progressive disclosure / context efficiency

- `SKILL.md` is the small always-on entrypoint.
- The large runtime client is execute-only: agents run
  `python3 scripts/esphome_dashboard.py <command> --help` instead of reading the
  script into context.
- Detailed API/policy/workflow docs live under `references/` and are loaded only
  when a task needs them.
- Build/upload output is summarized by default; full logs go to private files.
- `logs` and `run` are bounded by default so agent turns do not hang or flood
  context.

## Safety guarantees

- The Builder builds and deploys; this repo never invokes local
  ESPHome/PlatformIO/esptool for device firmware work.
- Device credentials stay in Builder `secrets.yaml`; configs use `!secret` refs;
  backup output surfaces any legacy inline-secret findings before git use.
- Edits are minimal-diff and guarded by lint/tests.
- `delete`, `rename`, and blanket `update-all` require explicit `--confirm` flags.
- https://esphome.io/ is the single source of truth for ESPHome documentation;
  https://github.com/esphome/esphome is the single source of truth for ESPHome
  code behavior. Issues and PRs may inform fixes, but docs/code win by default.
- Fleet updates follow the class-based policy in
  `esphome-builder/references/fleet-policy.md`.
