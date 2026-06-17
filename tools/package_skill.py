#!/usr/bin/env python3
"""Build or validate the ESPHome Builder skill archive.

The repository keeps one canonical editable source tree: ./esphome-builder/.
This script creates the generated release artifact in ./dist/ and refuses to
package local secrets, caches, backups, bytecode, or duplicate generated files.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "esphome-builder"
DIST_DIR = ROOT / "dist"
ARCHIVE = DIST_DIR / "esphome-builder-skill.tar.gz"
LICENSE = ROOT / "LICENSE"

REQUIRED = {
    "SKILL.md",
    ".env.example",
    ".gitignore",
    "scripts/esphome_dashboard.py",
    "references/dashboard-api.md",
    "references/fleet-policy.md",
    "references/workflows.md",
    "references/yaml-and-secrets.md",
    "assets/run-report.template.md",
}

FORBIDDEN_NAMES = {".env", "capabilities.json", ".DS_Store"}
ALLOWED_NAMES = {".env.example"}
FORBIDDEN_DIRS = {"__pycache__", "esphome-backup", ".git", ".hermes", "dist"}
FORBIDDEN_GLOBS = {"*.env", ".env.*", "*.pyc", "*.pyo", "*.log", "*.tar.gz"}


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def rel(path: Path) -> str:
    return path.relative_to(SKILL_DIR).as_posix()


def forbidden_name(name: str) -> bool:
    if name in ALLOWED_NAMES:
        return False
    if name in FORBIDDEN_NAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_GLOBS)


def is_forbidden(path: Path) -> bool:
    relative = path.relative_to(SKILL_DIR)
    parts = set(relative.parts)
    if parts & FORBIDDEN_DIRS:
        return True
    return forbidden_name(path.name)


def collect_files() -> list[Path]:
    if not SKILL_DIR.is_dir():
        fail(f"missing skill directory: {SKILL_DIR}")
    if not LICENSE.is_file():
        fail(f"missing repository license: {LICENSE}")

    missing = sorted(item for item in REQUIRED if not (SKILL_DIR / item).is_file())
    if missing:
        fail("missing required skill files: " + ", ".join(missing))

    files: list[Path] = []
    forbidden: list[str] = []
    for path in sorted(SKILL_DIR.rglob("*")):
        if path.is_symlink():
            forbidden.append(rel(path))
            continue
        if not path.is_file():
            continue
        if is_forbidden(path):
            forbidden.append(rel(path))
            continue
        files.append(path)

    if forbidden:
        fail("refusing to package forbidden local artifacts: " + ", ".join(forbidden))

    packaged = {rel(path) for path in files}
    missing_from_package = sorted(REQUIRED - packaged)
    if missing_from_package:
        fail("required files excluded from package: " + ", ".join(missing_from_package))

    return files


def scrub_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # Deterministic metadata keeps archive diffs small across rebuilds.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    if info.isfile():
        info.mode = 0o755 if info.name.endswith("/scripts/esphome_dashboard.py") else 0o644
    return info


def build_archive(files: list[Path], out_path: Path = ARCHIVE) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for path in files:
                    arcname = f"esphome-builder/{rel(path)}"
                    tar.add(path, arcname=arcname, filter=scrub_info)
                tar.add(LICENSE, arcname="esphome-builder/LICENSE", filter=scrub_info)
    return out_path


def check_archive_bytes(path: Path) -> None:
    with tarfile.open(path, "r:gz") as tar:
        names = set(tar.getnames())
    expected = {f"esphome-builder/{item}" for item in REQUIRED}
    expected.add("esphome-builder/LICENSE")
    missing = sorted(expected - names)
    if missing:
        fail("archive missing required files: " + ", ".join(missing))
    forbidden = [name for name in names if any(part in FORBIDDEN_DIRS for part in Path(name).parts)]
    forbidden += [name for name in names if forbidden_name(Path(name).name)]
    if forbidden:
        fail("archive contains forbidden artifacts: " + ", ".join(sorted(set(forbidden))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/check esphome-builder-skill.tar.gz")
    parser.add_argument("--check", action="store_true", help="validate package inputs without writing dist archive")
    parser.add_argument("--out", default=str(ARCHIVE), help="output archive path")
    args = parser.parse_args(argv)

    files = collect_files()
    if args.check:
        print(f"package check ok: {len(files)} files from esphome-builder/")
        return 0

    out_path = build_archive(files, Path(args.out))
    check_archive_bytes(out_path)
    print(f"wrote {out_path} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
