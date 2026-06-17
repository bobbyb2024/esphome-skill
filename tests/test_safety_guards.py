import contextlib
import io
import unittest

from tests._helpers import load_tool, patched


class SafetyGuardTests(unittest.TestCase):
    def test_delete_requires_matching_confirmation_before_remote_call(self):
        tool = load_tool()
        calls = []
        with patched(
            tool,
            conn_from=lambda args: object(),
            delete_config=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        ):
            with self.assertRaises(SystemExit):
                tool.main(["delete", "node"])
        self.assertEqual(calls, [])

    def test_rename_requires_matching_confirmation_before_remote_call(self):
        tool = load_tool()
        calls = []
        with patched(
            tool,
            conn_from=lambda args: object(),
            stream_command=lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
        ):
            with self.assertRaises(SystemExit):
                tool.main(["rename", "old", "new"])
        self.assertEqual(calls, [])

    def test_update_all_requires_explicit_confirmation_before_remote_call(self):
        tool = load_tool()
        calls = []
        stdout = io.StringIO()
        with patched(
            tool,
            conn_from=lambda args: object(),
            stream_command=lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
        ):
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit):
                tool.main(["update-all"])
        self.assertEqual(calls, [])

    def test_delete_confirmation_is_checked_before_connection_lookup(self):
        tool = load_tool()
        with patched(
            tool,
            conn_from=lambda args: (_ for _ in ()).throw(AssertionError("conn_from should not run")),
            delete_config=lambda *args, **kwargs: True,
        ):
            with self.assertRaises(SystemExit):
                tool.main(["delete", "node"])

    def test_confirmation_accepts_stem_when_argument_has_yaml_suffix(self):
        tool = load_tool()
        calls = []
        with patched(
            tool,
            conn_from=lambda args: object(),
            delete_config=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        ):
            code = tool.main(["delete", "node.yaml", "--confirm", "node"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
