import os
import unittest

from tests._helpers import load_tool, patched


def _ws_responses(*payloads):
    """Helper: prepend a server_version hello so BetaWSClient.connect() can discard it."""
    return [{"type": "server_version", "version": "2026.6.0b3"}] + list(payloads)


class BetaWebSocketTests(unittest.TestCase):
    def _make_fake_ws_client(self, canned_responses):
        responses = iter(canned_responses)
        sent_msgs = []

        class FakeWSClient:
            def __init__(self, conn, path, timeout=900):
                self.conn, self.path, self.timeout = conn, path, timeout
                self._responses = responses
                self._sent = sent_msgs

            def connect(self):
                return self

            def send_json(self, payload):
                sent_msgs.append(payload)

            def recv_json(self):
                try:
                    return next(self._responses)
                except StopIteration:
                    return None

            def set_timeout(self, t):
                pass

            def close(self):
                pass

        return FakeWSClient, sent_msgs

    def test_beta_get_config_command_format(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "result": "hello:\n  name: test\n"},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            result = tool.beta_get_config(conn, "test")

        self.assertEqual(result, "hello:\n  name: test\n")
        self.assertEqual(sent[0], {
            "command": "devices/get_config",
            "message_id": "1",
            "args": {"configuration": "test.yaml"},
        })

    def test_beta_update_config_sends_content_in_args(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "result": None},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            result = tool.beta_update_config(conn, "kitchen",
                                             "wifi:\n  ssid: !secret wifi_ssid\n")

        self.assertTrue(result)
        self.assertEqual(sent[0], {
            "command": "devices/update_config",
            "message_id": "1",
            "args": {"configuration": "kitchen.yaml",
                     "content": "wifi:\n  ssid: !secret wifi_ssid\n"},
        })

    def test_beta_compile_returns_job_id(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "result": {"job_id": "job-abc-123"}},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            job_id = tool.beta_compile(conn, "kitchen-streamer")

        self.assertEqual(job_id, "job-abc-123")
        self.assertEqual(sent[0], {
            "command": "firmware/compile",
            "message_id": "1",
            "args": {"configuration": "kitchen-streamer.yaml"},
        })

    def test_beta_follow_job_streams_events_and_reports_ok(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "event": "line", "data": "Compiling...\n"},
            {"message_id": "1", "event": "line", "data": "Linking...\n"},
            {"message_id": "1", "event": "line", "data": "SUCCESS\n"},
            {"message_id": "1", "event": "exit", "data": {"code": 0}},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            res = tool.beta_follow_job(conn, "job-1", mode="summary",
                                       name="test.yaml", action="compile")

        self.assertTrue(res["ok"])
        self.assertEqual(res["lines"], 3)
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["stopped"], "exit")
        self.assertEqual(sent[0], {
            "command": "firmware/follow_job",
            "message_id": "1",
            "args": {"job_id": "job-1"},
        })

    def test_beta_follow_job_detects_compile_failure(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "event": "line",
             "data": "src/main.cpp:42:1: error: 'foo' was not declared\n"},
            {"message_id": "1", "event": "exit", "data": {"code": 1}},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            res = tool.beta_follow_job(conn, "job-2", mode="summary",
                                       name="bad.yaml", action="compile")

        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 1)
        self.assertTrue(any("foo" in e for e in res["error_lines"]))

    def test_beta_ws_client_skips_server_version_hello(self):
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "result": "hello world"},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            beta = tool.BetaWSClient(conn).connect()
            mid = beta.send_command("devices/list")
            resp = beta.recv_json()
            beta.close()

        self.assertEqual(resp, {"message_id": "1", "result": "hello world"})
        self.assertEqual(sent[0]["command"], "devices/list")

    def test_get_config_falls_back_to_beta_on_html_response(self):
        tool = load_tool()
        http_calls = []
        beta_calls = []

        def fake_http(conn, method, path, params=None, body=None, headers=None, timeout=30):
            http_calls.append((method, path, params))
            return (200, {"Content-Type": "text/html"}, b"<!DOCTYPE html><html>...</html>")

        def fake_beta(conn, name):
            beta_calls.append(name)
            return "esphome:\n  name: test\n"

        conn = tool.Conn("http://builder:6052")
        with patched(tool, http_request=fake_http, beta_get_config=fake_beta):
            result = tool.get_config(conn, "test")

        self.assertEqual(result, "esphome:\n  name: test\n")
        self.assertEqual(http_calls, [("GET", "/edit", {"configuration": "test.yaml"})])
        self.assertEqual(beta_calls, ["test"])

    def test_post_config_falls_back_to_beta_on_html_response(self):
        tool = load_tool()
        http_calls = []
        beta_calls = []

        def fake_http(conn, method, path, params=None, body=None, headers=None, timeout=30):
            http_calls.append((method, path, params, body))
            return (200, {"Content-Type": "text/html"}, b"<!DOCTYPE html><html>...</html>")

        def fake_beta(conn, name, text):
            beta_calls.append((name, text))
            return True

        conn = tool.Conn("http://builder:6052")
        with patched(tool, http_request=fake_http, beta_update_config=fake_beta):
            result = tool._post_config(conn, "test", "content")

        self.assertTrue(result)
        self.assertEqual(http_calls[0], ("POST", "/edit", {"configuration": "test.yaml"}, "content"))
        self.assertEqual(beta_calls, [("test", "content")])

    def test_beta_compile_integration_flow(self):
        """Simulate a beta compile workflow starting from expand_targets."""
        tool = load_tool()
        Fake, sent = self._make_fake_ws_client(_ws_responses(
            {"message_id": "1", "result": {"job_id": "job-xyz"}},
        ))

        conn = tool.Conn("http://builder:6052")
        with patched(tool, WSClient=Fake):
            job_id = tool.beta_compile(conn, "kitchen-streamer")

        self.assertEqual(job_id, "job-xyz")
        self.assertEqual(sent[0], {
            "command": "firmware/compile",
            "message_id": "1",
            "args": {"configuration": "kitchen-streamer.yaml"},
        })


if __name__ == "__main__":
    unittest.main()
