import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "esphome-builder"
SCRIPT = SKILL / "scripts" / "esphome_dashboard.py"


class CliContractTests(unittest.TestCase):
    def test_help_exposes_expected_command_surface(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=SKILL,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        expected = {
            "connect", "enumerate", "list", "status", "info", "get", "put",
            "create", "clone", "delete", "rename", "validate", "compile",
            "clean", "upload", "logs", "run", "update-all", "classify",
            "secrets", "backup", "watch", "lint", "downloads",
        }
        for command in expected:
            self.assertIn(command, result.stdout)

    def test_skill_references_progressive_disclosure_paths(self):
        skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not read the script into context", skill_md)
        self.assertIn("python3 scripts/esphome_dashboard.py", skill_md)
        self.assertIn("references/workflows.md", skill_md)
        self.assertIn("references/fleet-policy.md", skill_md)
        self.assertTrue(SCRIPT.exists())
        self.assertTrue((SKILL / "references" / "workflows.md").exists())
        self.assertTrue((SKILL / "references" / "fleet-policy.md").exists())
        self.assertTrue((SKILL / "references" / "dashboard-api.md").exists())
        self.assertTrue((SKILL / "references" / "yaml-and-secrets.md").exists())
        self.assertTrue((SKILL / "assets" / "run-report.template.md").exists())

    def test_skill_declares_esphome_docs_and_code_sources_of_truth(self):
        skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("https://esphome.io/", skill_md)
        self.assertIn("https://github.com/esphome/esphome", skill_md)
        self.assertIn("single source of truth", skill_md.lower())
        self.assertIn("Issues and PRs", skill_md)


    def test_frontmatter_description_stays_lean(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        _, frontmatter, body = text.split("---", 2)
        self.assertIn("name: esphome-builder", frontmatter)
        description_lines = []
        in_description = False
        for line in frontmatter.splitlines():
            if line.startswith("description:"):
                in_description = True
                continue
            if in_description and line and not line.startswith("  "):
                break
            if in_description:
                description_lines.append(line.strip())
        self.assertLessEqual(len(" ".join(description_lines)), 1024)
        self.assertGreater(len(body.strip()), 0)


if __name__ == "__main__":
    unittest.main()
