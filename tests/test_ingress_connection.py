import os
import unittest

from tests._helpers import load_tool, patched


HA_TOKEN = "ha-" + "token"
SESSION = "session-123"
INGRESS_PATH = "/api/hassio_ingress/abc123"
ADDON_SLUG = "5c53de3b_esphome"


class EnvPatch:
    def __init__(self, updates, removals=()):
        self.updates = updates
        self.removals = removals
        self.old = {}

    def __enter__(self):
        keys = set(self.updates) | set(self.removals)
        self.old = {key: os.environ.get(key) for key in keys}
        for key in self.removals:
            os.environ.pop(key, None)
        os.environ.update(self.updates)

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class HomeAssistantIngressConnectionTests(unittest.TestCase):
    def test_env_example_documents_home_assistant_ingress_settings(self):
        root = os.path.dirname(os.path.dirname(__file__))
        env_example = os.path.join(root, "esphome-builder", ".env.example")
        with open(env_example, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("ESPHOME_HA_URL=", text)
        self.assertIn("ESPHOME_HA_TOKEN=", text)
        self.assertIn("ESPHOME_HA_ADDON_SLUG=5c53de3b_esphome", text)

    def test_conn_load_prefers_home_assistant_ingress_when_available(self):
        tool = load_tool()
        calls = []

        def fake_prepare(ha_url, ha_token, addon_slug, insecure=False):
            calls.append((ha_url, ha_token, addon_slug, insecure))
            return {
                "ha_origin": "http://ha.local:8123",
                "ingress_path": INGRESS_PATH,
                "session": SESSION,
                "addon_slug": addon_slug,
            }

        with EnvPatch({
            "ESPHOME_HA_URL": "http://ha.local:8123",
            "ESPHOME_HA_TOKEN": HA_TOKEN,
            "ESPHOME_HA_ADDON_SLUG": ADDON_SLUG,
            "ESPHOME_DASHBOARD_URL": "http://builder.local:6052",
        }):
            with patched(tool, prepare_ha_ingress=fake_prepare):
                conn = tool.Conn.load()

        self.assertEqual(calls, [("http://ha.local:8123", HA_TOKEN, ADDON_SLUG, False)])
        self.assertEqual(conn.transport, "ha-ingress")
        self.assertEqual(conn.origin, "http://ha.local:8123/api/hassio_ingress/abc123")
        self.assertEqual(conn.url_for("/devices"), "http://ha.local:8123/api/hassio_ingress/abc123/devices")
        self.assertEqual(conn.request_path("/logs"), "/api/hassio_ingress/abc123/logs")
        self.assertEqual(conn.auth_header(), {"Cookie": f"ingress_session={SESSION}"})

    def test_conn_load_falls_back_to_direct_dashboard_when_ingress_fails(self):
        tool = load_tool()
        calls = []

        def fake_prepare(ha_url, ha_token, addon_slug, insecure=False):
            calls.append((ha_url, ha_token, addon_slug, insecure))
            raise tool.HAIngressError("ingress unavailable")

        with EnvPatch({
            "ESPHOME_HA_URL": "http://ha.local:8123",
            "ESPHOME_HA_TOKEN": HA_TOKEN,
            "ESPHOME_DASHBOARD_URL": "http://builder.local:6052",
        }, removals=("ESPHOME_HA_ADDON_SLUG",)):
            with patched(tool, prepare_ha_ingress=fake_prepare):
                conn = tool.Conn.load()

        self.assertEqual(calls, [("http://ha.local:8123", HA_TOKEN, ADDON_SLUG, False)])
        self.assertEqual(conn.transport, "direct")
        self.assertEqual(conn.origin, "http://builder.local:6052")
        self.assertEqual(conn.url_for("/devices"), "http://builder.local:6052/devices")

    def test_http_request_sends_ingress_cookie_and_prefixed_url(self):
        tool = load_tool()
        captured = {}

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(self):
                return b"{}"

        class FakeOpener:
            def open(self, req, timeout=30):
                captured["url"] = req.full_url
                captured["headers"] = dict(req.header_items())
                captured["timeout"] = timeout
                return FakeResponse()

        conn = tool.Conn(
            "http://ha.local:8123",
            transport="ha-ingress",
            path_prefix=INGRESS_PATH,
            ingress_session=SESSION,
        )
        with patched(tool, _opener=lambda conn: FakeOpener()):
            status, headers, body = tool.http_request(conn, "GET", "/devices", params={"q": "x"}, timeout=7)

        self.assertEqual(status, 200)
        self.assertEqual(body, b"{}")
        self.assertEqual(captured["url"], "http://ha.local:8123/api/hassio_ingress/abc123/devices?q=x")
        self.assertEqual(captured["headers"].get("Cookie"), f"ingress_session={SESSION}")
        self.assertEqual(captured["timeout"], 7)

    def test_ha_supervisor_api_uses_authenticated_websocket_command(self):
        tool = load_tool()
        sent = []

        class FakeWS:
            def __init__(self):
                self.responses = iter([
                    {"type": "auth_required"},
                    {"type": "auth_ok"},
                    {"id": 1, "type": "result", "success": True, "result": {"session": SESSION}},
                ])

            def connect(self):
                return self

            def send_json(self, payload):
                sent.append(payload)

            def recv_json(self):
                return next(self.responses)

            def close(self):
                sent.append({"closed": True})

        class FakeWSClient:
            def __init__(self, conn, path, timeout=900):
                self.conn = conn
                self.path = path
                self.timeout = timeout

            def connect(self):
                self.conn_seen = self.conn
                self.path_seen = self.path
                return FakeWS()

        ha_conn = tool.Conn("http://ha.local:8123")
        with patched(tool, WSClient=FakeWSClient):
            result = tool.ha_supervisor_api(ha_conn, HA_TOKEN, "/ingress/session", "post")

        self.assertEqual(result, {"session": SESSION})
        self.assertEqual(sent[0], {"type": "auth", "access_token": HA_TOKEN})
        self.assertEqual(sent[1], {
            "id": 1,
            "type": "supervisor/api",
            "endpoint": "/ingress/session",
            "method": "post",
        })
        self.assertEqual(sent[-1], {"closed": True})
    def test_prepare_ha_ingress_resolves_addon_info_then_creates_session(self):
        tool = load_tool()
        calls = []

        def fake_supervisor_api(ha_conn, ha_token, endpoint, method="get", *, data=None, params=None):
            calls.append((ha_conn.base_origin, ha_token, endpoint, method, data, params))
            if endpoint == f"/addons/{ADDON_SLUG}/info":
                return {"ingress_url": INGRESS_PATH + "/"}
            if endpoint == "/ingress/session":
                return {"session": SESSION}
            raise AssertionError(endpoint)

        with patched(tool, ha_supervisor_api=fake_supervisor_api):
            result = tool.prepare_ha_ingress("http://ha.local:8123", HA_TOKEN, ADDON_SLUG)

        self.assertEqual(result, {
            "ha_origin": "http://ha.local:8123",
            "ingress_path": INGRESS_PATH,
            "session": SESSION,
            "addon_slug": ADDON_SLUG,
        })
        self.assertEqual(calls, [
            ("http://ha.local:8123", HA_TOKEN, f"/addons/{ADDON_SLUG}/info", "get", None, None),
            ("http://ha.local:8123", HA_TOKEN, "/ingress/session", "post", None, None),
        ])


if __name__ == "__main__":
    unittest.main()
