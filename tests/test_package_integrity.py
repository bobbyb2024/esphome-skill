import hashlib
import os
import subprocess
import sys
import tempfile
import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "esphome-builder"
PACKAGE_TOOL = ROOT / "tools" / "package_skill.py"


class PackageIntegrityTests(unittest.TestCase):
    def current_repo_files(self):
        skip_dirs = {".git", ".hermes", "dist", "__pycache__"}
        files = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if path.suffix == ".pyc":
                continue
            files.append(rel)
        return sorted(files)

    def test_no_generated_tarball_or_duplicate_legacy_source_tree(self):
        self.assertFalse((ROOT / "esphome-builder-skill.tar.gz").exists())
        self.assertFalse((ROOT / "esphome-builder-skill").exists())
        self.assertFalse((ROOT / "esphome_dashboard.py").exists())
        self.assertFalse((ROOT / "SKILL.md").exists())
        self.assertFalse((ROOT / "dashboard-api.md").exists())
        self.assertFalse((ROOT / "workflows.md").exists())

    def test_current_tree_has_no_duplicate_file_content(self):
        groups = {}
        for rel in self.current_repo_files():
            data = (ROOT / rel).read_bytes()
            # Ignore tiny empty files if any appear; exact empty duplicates are not useful signal.
            if not data:
                continue
            groups.setdefault(hashlib.sha256(data).hexdigest(), []).append(str(rel))
        duplicates = [paths for paths in groups.values() if len(paths) > 1]
        self.assertEqual(duplicates, [])

    def test_package_check_passes_and_contains_only_skill_tree(self):
        self.assertTrue(PACKAGE_TOOL.exists())
        result = subprocess.run(
            [sys.executable, str(PACKAGE_TOOL), "--check"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("package check ok", result.stdout)

    def test_package_check_rejects_env_like_local_artifacts(self):
        for filename in (".env.local", "private.env"):
            artifact = SKILL / filename
            try:
                artifact.write_text("DUMMY=value\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(PACKAGE_TOOL), "--check"],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(result.returncode, 0, filename)
                self.assertIn(filename, result.stdout + result.stderr)
            finally:
                artifact.unlink(missing_ok=True)

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink not supported")
    def test_package_check_rejects_symlinks(self):
        link = SKILL / "references" / "linked-workflows.md"
        try:
            os.symlink(SKILL / "references" / "workflows.md", link)
            result = subprocess.run(
                [sys.executable, str(PACKAGE_TOOL), "--check"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("linked-workflows.md", result.stdout + result.stderr)
        finally:
            link.unlink(missing_ok=True)


    def test_package_archive_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.tar.gz"
            second = Path(tmp) / "second.tar.gz"
            for out in (first, second):
                result = subprocess.run(
                    [sys.executable, str(PACKAGE_TOOL), "--out", str(out)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )


    def test_package_archive_contains_license(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "skill.tar.gz"
            result = subprocess.run(
                [sys.executable, str(PACKAGE_TOOL), "--out", str(out)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with tarfile.open(out, "r:gz") as tar:
                self.assertIn("esphome-builder/LICENSE", tar.getnames())


    def test_canonical_skill_files_exist(self):
        required = [
            SKILL / "SKILL.md",
            SKILL / ".env.example",
            SKILL / ".gitignore",
            SKILL / "scripts" / "esphome_dashboard.py",
            SKILL / "references" / "dashboard-api.md",
            SKILL / "references" / "fleet-policy.md",
            SKILL / "references" / "workflows.md",
            SKILL / "references" / "yaml-and-secrets.md",
            SKILL / "assets" / "run-report.template.md",
        ]
        for path in required:
            self.assertTrue(path.exists(), str(path))


if __name__ == "__main__":
    unittest.main()
