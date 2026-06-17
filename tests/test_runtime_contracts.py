import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests._helpers import load_tool, patched


class RuntimeContractTests(unittest.TestCase):
    def test_create_from_file_local_secret_refusal_has_no_wizard_side_effect(self):
        tool = load_tool()
        wizard_calls = []
        unsafe_yaml = "wifi:\n  password: literal\n"
        with patched(
            tool,
            exists=lambda conn, name: False,
            create_via_wizard=lambda *args, **kwargs: wizard_calls.append((args, kwargs)) or {"configuration": "node.yaml"},
        ):
            with self.assertRaises(SystemExit):
                tool.create_from_file(None, "node", unsafe_yaml, allow_secrets=False)
        self.assertEqual(wizard_calls, [])

    def test_batch_targets_keep_port_for_every_spawn(self):
        tool = load_tool()
        spawns = []

        def fake_stream(conn, ws_path, spawn, **kwargs):
            spawns.append(dict(spawn))
            return {
                "command": kwargs.get("action"),
                "name": kwargs.get("name"),
                "ok": True,
                "lines": 0,
                "exit_code": 0,
                "stopped": "exit",
                "log_file": None,
            }

        class Args:
            name = None
            match = "*"

        with patched(
            tool,
            expand_targets=lambda conn, name, match: ["one.yaml", "two.yaml"],
            stream_command=fake_stream,
        ):
            code = tool.run_target_batch(None, Args(), "/upload", "upload", mode="summary", port="OTA")
        self.assertEqual(code, 0)
        self.assertEqual(spawns, [
            {"configuration": "one.yaml", "port": "OTA"},
            {"configuration": "two.yaml", "port": "OTA"},
        ])

    def test_batch_json_mode_emits_single_aggregate_document(self):
        tool = load_tool()

        def fake_stream(conn, ws_path, spawn, **kwargs):
            return {
                "command": kwargs.get("action"),
                "name": kwargs.get("name"),
                "ok": True,
                "lines": 0,
                "exit_code": 0,
                "stopped": "exit",
                "log_file": None,
            }

        class Args:
            name = None
            match = "*"

        stdout = io.StringIO()
        with patched(
            tool,
            _JSON=True,
            expand_targets=lambda conn, name, match: ["one.yaml", "two.yaml"],
            stream_command=fake_stream,
        ):
            with contextlib.redirect_stdout(stdout):
                code = tool.run_target_batch(None, Args(), "/compile", "compile", mode="summary")
        self.assertEqual(code, 0)
        text = stdout.getvalue()
        decoded = json.loads(text)
        self.assertEqual(decoded["command"], "compile")
        self.assertEqual(len(decoded["results"]), 2)
        decoder = json.JSONDecoder()
        _, end = decoder.raw_decode(text)
        self.assertEqual(text[end:].strip(), "")

    def test_run_defaults_are_bounded_unless_follow_is_set(self):
        tool = load_tool()
        calls = []

        def fake_stream(conn, ws_path, spawn, **kwargs):
            calls.append((ws_path, dict(spawn), dict(kwargs)))
            return {
                "command": kwargs.get("action"),
                "name": kwargs.get("name"),
                "ok": True,
                "lines": 0,
                "exit_code": 0,
                "stopped": "lines",
                "log_file": None,
            }

        stdout = io.StringIO()
        with patched(tool, conn_from=lambda args: object(), stream_command=fake_stream, _JSON=False):
            with contextlib.redirect_stdout(stdout):
                code = tool.main(["run", "node", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        _, _, kwargs = calls[0]
        self.assertEqual(kwargs["duration"], 60)
        self.assertEqual(kwargs["lines"], 80)

    def test_backup_rejects_path_traversal_configuration_names(self):
        tool = load_tool()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "backup"
            outside = root / "outside.yaml"
            with patched(
                tool,
                configured_filenames=lambda conn: ["../outside.yaml"],
                get_config=lambda conn, name: "esphome:\n  name: node\n",
            ):
                with self.assertRaises(SystemExit):
                    tool.backup_all(type("Conn", (), {"origin": "http://builder:6052"})(), str(out_dir))
            self.assertFalse(outside.exists())

    def test_run_stopped_by_bounds_before_exit_is_not_success(self):
        tool = load_tool()

        class FakeWS:
            def connect(self):
                return self

            def send_json(self, payload):
                pass

            def set_timeout(self, timeout):
                pass

            def recv_json(self):
                return {"event": "line", "data": "Compiling node...\n"}

            def close(self):
                pass

        class FakeWSClient:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                return FakeWS()

        with patched(tool, WSClient=FakeWSClient):
            result = tool.stream_command(
                object(),
                "/run",
                {"configuration": "node.yaml", "port": "OTA"},
                mode="stream",
                action="run",
                name="node.yaml",
                lines=1,
            )
        self.assertEqual(result["stopped"], "lines")
        self.assertIsNone(result["exit_code"])
        self.assertFalse(result["ok"])

    def test_create_from_file_second_save_failure_reports_clean_error(self):
        tool = load_tool()
        calls = []

        def fake_post(conn, name, text):
            calls.append(name)
            if len(calls) == 1:
                raise tool.BuilderHTTPError(404, b"not found")
            raise tool.BuilderHTTPError(500, b"second save failed")

        with patched(
            tool,
            exists=lambda conn, name: False,
            _post_config=fake_post,
            create_via_wizard=lambda *args, **kwargs: {"configuration": "node.yaml"},
        ):
            with self.assertRaises(SystemExit):
                tool.create_from_file(None, "node", "esphome:\n  name: node\n")
        self.assertEqual(calls, ["node", "node"])

    def test_backup_json_summarizes_inline_secret_findings(self):
        tool = load_tool()
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with patched(
                tool,
                conn_from=lambda args: type("Conn", (), {"origin": "http://builder:6052"})(),
                configured_filenames=lambda conn: ["node.yaml"],
                get_config=lambda conn, name: "wifi:\n  password: literal\n",
                _JSON=False,
            ):
                with contextlib.redirect_stdout(stdout):
                    code = tool.main(["backup", "--out", tmp, "--json"])
        self.assertEqual(code, 0)
        decoded = json.loads(stdout.getvalue())
        self.assertEqual(decoded["inline_secret_findings_total"], 1)
        self.assertEqual(decoded["files"][0]["inline_secret_findings"], 1)


if __name__ == "__main__":
    unittest.main()
