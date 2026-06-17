import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "esphome-builder" / "scripts" / "esphome_dashboard.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("esphome_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeHelperTests(unittest.TestCase):
    def test_lint_flags_inline_secrets_but_allows_secret_refs(self):
        tool = load_tool()
        bad = "wifi:\n  ssid: MyWifi\n  password: hunter2\napi:\n  encryption:\n    key: abc123\n"
        good = "wifi:\n  ssid: !secret wifi_ssid\n  password: !secret wifi_password\napi:\n  encryption:\n    key: !secret api_key_node\n"
        findings = tool.lint_yaml(bad)
        self.assertGreaterEqual(len(findings), 2)
        self.assertEqual(tool.lint_yaml(good), [])
        self.assertEqual(tool.secret_refs(good), ["api_key_node", "wifi_password", "wifi_ssid"])

    def test_classification_policy_examples(self):
        tool = load_tool()
        cases = {
            "bluetooth-proxy": "esp32_ble_tracker:\nbluetooth_proxy:\nsensor:\n  - platform: uptime\n",
            "sendspin-player": "media_player:\n  - platform: sendspin\nbluetooth_proxy:\n",
            "other-multirole": "bluetooth_proxy:\nswitch:\n  - platform: gpio\n    pin: 5\n",
            "other-generic": "switch:\n  - platform: gpio\n    pin: 5\n",
        }
        self.assertEqual(tool.classify_text(cases["bluetooth-proxy"])[0], "bluetooth-proxy")
        self.assertEqual(tool.classify_text(cases["sendspin-player"])[0], "sendspin-player")
        self.assertEqual(tool.classify_text(cases["other-multirole"])[0], "other")
        self.assertEqual(tool.classify_text(cases["other-generic"])[0], "other")

    def test_clone_config_changes_only_identity_and_preserves_secret_refs(self):
        tool = load_tool()
        source = (
            "esphome:\n"
            "  name: garage\n"
            "  friendly_name: Garage\n"
            "# comment must survive\n"
            "api:\n"
            "  encryption:\n"
            "    key: !secret api_key_garage\n"
            "ota:\n"
            "  - platform: esphome\n"
            "    password: !secret ota_password_garage\n"
            "wifi:\n"
            "  password: !secret wifi_password\n"
        )
        saved = {}
        old_exists, old_get, old_put = tool.exists, tool.get_config, tool.put_config
        try:
            tool.exists = lambda conn, name: False
            tool.get_config = lambda conn, name: source
            tool.put_config = lambda conn, name, text, **kwargs: saved.setdefault(name, text) or True
            changed, cloned = tool.clone_config(None, "garage", "garage-2", friendly_name="Garage 2")
        finally:
            tool.exists, tool.get_config, tool.put_config = old_exists, old_get, old_put

        self.assertIn("  name: garage-2\n", cloned)
        self.assertIn("  friendly_name: Garage 2\n", cloned)
        self.assertIn("# comment must survive\n", cloned)
        self.assertIn("key: !secret api_key_garage", cloned)
        self.assertIn("password: !secret ota_password_garage", cloned)
        self.assertEqual(saved["garage-2"], cloned)
        self.assertEqual([line for line, _, _ in changed], [2, 3])

    def test_info_command_fetches_device_info_once(self):
        tool = load_tool()
        calls = []
        old_conn_from, old_get_json = tool.conn_from, tool.get_json
        try:
            tool.conn_from = lambda args: object()

            def fake_get_json(conn, path, params=None):
                calls.append((path, params))
                return {"name": params["configuration"]}

            tool.get_json = fake_get_json
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = tool.main(["info", "node", "--json"])
        finally:
            tool.conn_from, tool.get_json = old_conn_from, old_get_json
            tool._JSON = False

        self.assertEqual(code, 0)
        self.assertEqual(calls, [("/info", {"configuration": "node.yaml"})])
        self.assertIn('"name": "node.yaml"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
