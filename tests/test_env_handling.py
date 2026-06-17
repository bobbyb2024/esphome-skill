import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "esphome-builder" / "scripts" / "esphome_dashboard.py"
ENV_EXAMPLE = ROOT / "esphome-builder" / ".env.example"
PASSWORD_KEY = "ESPHOME_DASHBOARD_" + "PASSWORD"


def load_tool():
    spec = importlib.util.spec_from_file_location("esphome_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnvHandlingTests(unittest.TestCase):
    def test_env_example_has_blank_password_placeholder(self):
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{PASSWORD_KEY}=", lines)
        self.assertNotIn(f"{PASSWORD_KEY}=***", lines)

    def test_parse_env_file_supports_quotes_export_and_blank_values(self):
        tool = load_tool()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\n"
                "export ESPHOME_DASHBOARD_URL='http://builder:6052'\n"
                "ESPHOME_DASHBOARD_USERNAME=\"admin\"\n"
                f"{PASSWORD_KEY}=\n"
                "ESPHOME_DASHBOARD_INSECURE=true\n",
                encoding="utf-8",
            )
            parsed = tool.parse_env_file(str(path))
        self.assertEqual(parsed["ESPHOME_DASHBOARD_URL"], "http://builder:6052")
        self.assertEqual(parsed["ESPHOME_DASHBOARD_USERNAME"], "admin")
        self.assertEqual(parsed[PASSWORD_KEY], "")
        self.assertEqual(parsed["ESPHOME_DASHBOARD_INSECURE"], "true")

    def test_write_env_file_preserves_comments_updates_once_and_chmods_600(self):
        tool = load_tool()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# keep me\n"
                "ESPHOME_DASHBOARD_URL=http://old:6052\n"
                "ESPHOME_DASHBOARD_URL=http://duplicate:6052\n"
                "OTHER=value\n",
                encoding="utf-8",
            )
            tool.write_env_file(str(path), {"ESPHOME_DASHBOARD_URL": "http://new:6052"})
            text = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
        self.assertIn("# keep me", text)
        self.assertIn("OTHER=value", text)
        self.assertEqual(text.count("ESPHOME_DASHBOARD_URL="), 1)
        self.assertIn("ESPHOME_DASHBOARD_URL=http://new:6052", text)
        self.assertEqual(mode, 0o600)

    def test_load_env_files_does_not_override_real_environment(self):
        tool = load_tool()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("ESPHOME_DASHBOARD_URL=http://file:6052\n", encoding="utf-8")
            old = os.environ.get("ESPHOME_DASHBOARD_URL")
            try:
                os.environ["ESPHOME_DASHBOARD_URL"] = "http://real:6052"
                tool.load_env_files(explicit=str(path))
                self.assertEqual(os.environ["ESPHOME_DASHBOARD_URL"], "http://real:6052")
            finally:
                if old is None:
                    os.environ.pop("ESPHOME_DASHBOARD_URL", None)
                else:
                    os.environ["ESPHOME_DASHBOARD_URL"] = old


if __name__ == "__main__":
    unittest.main()
