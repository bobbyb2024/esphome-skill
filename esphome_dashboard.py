#!/usr/bin/env python3
"""
esphome_dashboard.py — a powerful, zero-dependency client for the ESPHome
Builder (ESPHome Dashboard) HTTP + WebSocket API, with fleet management on top.

The Builder builds and deploys; this tool never compiles/flashes locally. It
performs minimal YAML edits, keeps secrets in the Builder (`!secret`), and adds
a policy/classification layer for safe fleet operations.

Design notes for agents
-----------------------
* Stdlib only. No pip installs. Python 3.9+.
* Self-documenting: run `<command> --help`. Do NOT read this file into context;
  run it. Detail lives in the bundled references, loaded only when needed.
* Build output is summarized by default (full log written to a file, only a
  compact result printed) so compiles don't flood the agent's context. Use
  --verbose to stream, --log-file to choose the path.
* `logs`/`run` are bounded by default (--duration/--lines/--until) so streaming
  can't hang a turn; use --follow for unbounded.
* --json on any command emits machine-parseable output.

Configuration (first match wins)
---------------------------------
All connection settings live in environment variables, sourced (in precedence
order) from: CLI flags > the real environment > a `.env` file. Nothing is
hardcoded; nothing is stored outside `.env`.

* CLI flags: --url --username --password --insecure --env-file
* Variables (set in the real env or in `.env`):
    ESPHOME_DASHBOARD_URL        e.g. http://HOST:6052
    ESPHOME_DASHBOARD_USERNAME   (optional)
    ESPHOME_DASHBOARD_PASSWORD   (optional)
    ESPHOME_DASHBOARD_INSECURE   true/false (self-signed TLS)
    ESPHOME_BACKUP_DIR           (optional) default backup dir
    ESPHOME_BUILDER_LOG_DIR      (optional) where build logs are written
* `.env` search order (first found wins, real env always wins over files):
    --env-file PATH  >  $ESPHOME_BUILDER_ENV  >  ./.env  >  ~/.config/esphome-builder/.env
* `connect --save` writes these variables into the `.env` file (chmod 600),
  preserving any other lines/comments already in it. The agent should not need
  to handle the password — have the user populate `.env` from `.env.example`.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import fnmatch
import json
import os
import re
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

ENV_FILE_ENV = "ESPHOME_BUILDER_ENV"
DEFAULT_ENV = os.path.expanduser("~/.config/esphome-builder/.env")
CAPS_CACHE = os.path.expanduser("~/.config/esphome-builder/capabilities.json")
DEFAULT_PORT = 6052

SECRET_HINT_KEYS = ("password", "psk", "ssid", "key", "api_key", "token",
                    "ota_password", "encryption")
SECRET_REF_RE = re.compile(r"!secret\s+([A-Za-z0-9_.\-]+)")
ERROR_LINE_RE = re.compile(
    r"(error|failed|fatal|undefined reference|cannot|could not|traceback|"
    r"exception|invalid|no such|not found)", re.I)

# Top-level components that mean the device drives real hardware (used by the
# multi-role tiebreaker in classification).
HARDWARE_KEYS = {"switch", "light", "cover", "climate", "fan", "lock", "valve",
                 "display", "output", "servo", "stepper", "pwm", "dac",
                 "media_player", "speaker", "microphone", "camera"}
SENDSPIN_RE = re.compile(r"\b(sendspin|resonate)\b", re.I)

_JSON = False  # set from --json


# --------------------------------------------------------------------------- #
# printing
# --------------------------------------------------------------------------- #
def err(msg):
    print(f"error: {msg}", file=sys.stderr)


def die(msg, code=1):
    if _JSON:
        print(json.dumps({"ok": False, "error": msg}))
    else:
        err(msg)
    sys.exit(code)


def out(obj_for_json, text_for_human=None):
    if _JSON:
        print(json.dumps(obj_for_json, indent=2, sort_keys=True, default=str))
    elif text_for_human is not None:
        print(text_for_human)


def progress(msg):
    if not _JSON:
        print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# .env handling (zero-dependency)
# --------------------------------------------------------------------------- #
_ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def parse_env_file(path):
    data = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key:
                data[key] = val
    return data


def load_env_files(explicit=None):
    """Populate os.environ from .env files without overriding real env vars.
    Precedence (high->low): real env, --env-file/$ESPHOME_BUILDER_ENV, ./.env,
    ~/.config/esphome-builder/.env. Returns the list of files loaded."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get(ENV_FILE_ENV):
        candidates.append(os.environ[ENV_FILE_ENV])
    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(DEFAULT_ENV)
    loaded = []
    for path in candidates:
        if path and os.path.exists(path):
            for k, v in parse_env_file(path).items():
                os.environ.setdefault(k, v)  # real env + earlier files win
            loaded.append(path)
    return loaded


def default_save_env_path(explicit=None):
    if explicit:
        return explicit
    if os.environ.get(ENV_FILE_ENV):
        return os.environ[ENV_FILE_ENV]
    cwd_env = os.path.join(os.getcwd(), ".env")
    if os.path.exists(cwd_env):
        return cwd_env
    return DEFAULT_ENV


def write_env_file(path, updates):
    """Update KEY=VALUE pairs in a .env file, preserving other lines/comments."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    seen = set()
    new_lines = []
    for line in lines:
        m = _ENV_LINE_RE.match(line.strip())
        if m and m.group(1) in updates:
            k = m.group(1)
            if k not in seen:
                new_lines.append(f"{k}={updates[k]}")
                seen.add(k)
            # drop duplicate definitions
        else:
            new_lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(new_lines).rstrip("\n") + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #
class Conn:
    def __init__(self, url, username="", password="", insecure=False):
        url = url.strip()
        if "://" not in url:
            url = "http://" + url
        p = urllib.parse.urlsplit(url)
        self.scheme = p.scheme or "http"
        self.host = p.hostname or ""
        if not self.host:
            raise ValueError(f"could not parse host from URL: {url!r}")
        self.port = int(p.port or (443 if self.scheme == "https" else DEFAULT_PORT))
        self.username = username or ""
        self.password = password or ""
        self.insecure = bool(insecure)

    @property
    def origin(self):
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def ws_scheme(self):
        return "wss" if self.scheme == "https" else "ws"

    def auth_header(self):
        if self.username:
            raw = f"{self.username}:{self.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}

    def ssl_context(self):
        ctx = ssl.create_default_context()
        if self.insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @classmethod
    def load(cls, url=None, username=None, password=None, insecure=None):
        # All settings come from variables (real env or a loaded .env file).
        url = url or os.environ.get("ESPHOME_DASHBOARD_URL")
        username = username if username is not None else os.environ.get("ESPHOME_DASHBOARD_USERNAME")
        password = password if password is not None else os.environ.get("ESPHOME_DASHBOARD_PASSWORD")
        if insecure is None:
            insecure = os.environ.get("ESPHOME_DASHBOARD_INSECURE", "").lower() in ("1", "true", "yes")
        if not url:
            die("no ESPHome Builder URL. Set ESPHOME_DASHBOARD_URL in your .env "
                "(copy .env.example), pass --url, or run `connect --save`.")
        return cls(url, username or "", password or "", insecure)

    def save(self, path=None):
        """Write connection variables into a .env file, preserving other lines."""
        path = path or default_save_env_path()
        updates = {"ESPHOME_DASHBOARD_URL": self.origin}
        # Only write keys we actually have, so we never invent empty creds.
        if self.username:
            updates["ESPHOME_DASHBOARD_USERNAME"] = self.username
        if self.password:
            updates["ESPHOME_DASHBOARD_PASSWORD"] = self.password
        if self.insecure:
            updates["ESPHOME_DASHBOARD_INSECURE"] = "true"
        write_env_file(path, updates)
        return path


# --------------------------------------------------------------------------- #
# HTTP (no redirect following, so login redirects are detectable)
# --------------------------------------------------------------------------- #
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENERS = {}


def _opener(conn):
    if conn.insecure not in _OPENERS:
        handlers = [_NoRedirect(), urllib.request.HTTPSHandler(context=conn.ssl_context())]
        _OPENERS[conn.insecure] = urllib.request.build_opener(*handlers)
    return _OPENERS[conn.insecure]


def http_request(conn, method, path, *, params=None, body=None, headers=None, timeout=30):
    url = conn.origin + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    hdrs = {"Accept": "application/json, text/plain, */*"}
    hdrs.update(conn.auth_header())
    if headers:
        hdrs.update(headers)
    data = None if body is None else (body if isinstance(body, bytes) else body.encode())
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        resp = _opener(conn).open(req, timeout=timeout)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b"")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError):
            die(f"TLS error talking to {conn.origin}: {reason}. For a self-signed "
                f"cert pass --insecure.")
        die(f"cannot reach {conn.origin}: {reason}")
    except (socket.timeout, TimeoutError):
        die(f"timed out talking to {conn.origin}")


def _login_redirect(status, hdrs):
    return status in (301, 302, 303, 307, 308) and "login" in (hdrs.get("Location") or "").lower()


def get_text(conn, path, params=None):
    s, h, b = http_request(conn, "GET", path, params=params)
    if s == 401 or _login_redirect(s, h):
        die("authentication required/failed. Provide --username/--password.")
    if s >= 400:
        die(f"GET {path} -> HTTP {s}: {b[:300].decode('utf-8','replace')}")
    return b.decode("utf-8", "replace")


def get_json(conn, path, params=None):
    return json.loads(get_text(conn, path, params=params) or "null")


# --------------------------------------------------------------------------- #
# WebSocket (stdlib, text frames, client-masked)
# --------------------------------------------------------------------------- #
class WSTimeout(Exception):
    pass


class WSClient:
    OP_CONT, OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x8, 0x9, 0xA

    def __init__(self, conn, path, timeout=900):
        self.conn, self.path, self.timeout = conn, path, timeout
        self.sock, self._buf = None, b""

    def connect(self):
        raw = socket.create_connection((self.conn.host, self.conn.port), timeout=30)
        if self.conn.ws_scheme == "wss":
            raw = self.conn.ssl_context().wrap_socket(raw, server_hostname=self.conn.host)
        raw.settimeout(self.timeout)
        self.sock = raw
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {self.path} HTTP/1.1", f"Host: {self.conn.host}:{self.conn.port}",
                 "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
                 f"Origin: {self.conn.origin}"]
        for k, v in self.conn.auth_header().items():
            lines.append(f"{k}: {v}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        header = self._read_until(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0].decode("latin1")
        if "101" not in status:
            self.close()
            if "401" in status:
                die("websocket auth failed; check --username/--password.")
            die(f"websocket handshake failed: {status}")
        return self

    def set_timeout(self, t):
        self.sock.settimeout(t)

    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self._buf += chunk
        idx = self._buf.find(marker)
        if idx < 0:
            d, self._buf = self._buf, b""
            return d
        end = idx + len(marker)
        d, self._buf = self._buf[:end], self._buf[end:]
        return d

    def _read_exactly(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("socket closed mid-frame")
            self._buf += chunk
        d, self._buf = self._buf[:n], self._buf[n:]
        return d

    def send_json(self, obj):
        payload = json.dumps(obj).encode()
        h = bytearray([0x80 | self.OP_TEXT])
        n = len(payload)
        if n < 126:
            h.append(0x80 | n)
        elif n < (1 << 16):
            h.append(0x80 | 126); h += struct.pack(">H", n)
        else:
            h.append(0x80 | 127); h += struct.pack(">Q", n)
        mask = os.urandom(4)
        h += mask
        self.sock.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_frame(self):
        b0, b1 = self._read_exactly(2)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack(">H", self._read_exactly(2))
        elif n == 127:
            (n,) = struct.unpack(">Q", self._read_exactly(8))
        mask = self._read_exactly(4) if masked else b""
        payload = self._read_exactly(n) if n else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def recv_json(self):
        data = b""
        while True:
            try:
                opcode, payload = self._recv_frame()
            except socket.timeout:
                raise WSTimeout()
            except (ConnectionError, OSError):
                return None
            if opcode in (self.OP_TEXT, self.OP_CONT):
                data += payload
                try:
                    return json.loads(data.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
            elif opcode == self.OP_PING:
                self._pong(payload)
            elif opcode == self.OP_CLOSE:
                return None

    def _pong(self, payload):
        h = bytearray([0x80 | self.OP_PONG, 0x80 | len(payload)])
        mask = os.urandom(4)
        h += mask
        self.sock.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass


def _default_log_path(name, action):
    base = os.environ.get("ESPHOME_BUILDER_LOG_DIR",
                          os.path.join(os.path.expanduser("~"), ".cache",
                                       "esphome-builder", "logs"))
    os.makedirs(base, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name or action)
    return os.path.join(base, f"{safe}-{action}-{stamp}.log")


def stream_command(conn, ws_path, spawn, *, mode="summary", tail=40, log_path=None,
                   duration=None, lines=None, until=None, action="cmd", name=""):
    """Run a Builder command over WebSocket.

    mode="summary": full output -> log file; returns only a compact result and
        (on failure) the error/tail lines. Best for compile/upload (context-cheap).
    mode="stream": print every line live to stdout. Best for logs/run.

    Bounds (logs/run): stop after `duration` seconds, `lines` lines, or when a
    line matches `until` (regex). Returns a dict result.
    """
    until_re = re.compile(until) if until else None
    if log_path is None and mode == "summary":
        log_path = _default_log_path(name, action)
    logf = open(log_path, "w", encoding="utf-8") if log_path else None
    recent = deque(maxlen=max(tail, 60))
    errors = []
    count = 0
    code, stopped = None, "closed"
    import time as _t
    deadline = (_t.monotonic() + duration) if duration else None
    ws = WSClient(conn, ws_path).connect()
    try:
        ws.send_json({"type": "spawn", **spawn})
        while True:
            if deadline is not None:
                remaining = deadline - _t.monotonic()
                if remaining <= 0:
                    stopped = "duration"; break
                ws.set_timeout(max(0.2, min(remaining, 30)))
            try:
                msg = ws.recv_json()
            except WSTimeout:
                if deadline is not None and _t.monotonic() >= deadline:
                    stopped = "duration"; break
                continue
            if msg is None:
                stopped = "closed"; break
            ev = msg.get("event")
            if ev == "line":
                line = msg.get("data", "")
                count += 1
                if logf:
                    logf.write(line)
                recent.append(line)
                if ERROR_LINE_RE.search(line) and len(errors) < 60:
                    errors.append(line.rstrip("\n"))
                if mode == "stream" and not _JSON:
                    sys.stdout.write(line); sys.stdout.flush()
                elif mode == "summary" and count % 100 == 0:
                    progress(f"  …{count} lines")
                if until_re and until_re.search(line):
                    stopped = "until"; break
                if lines and count >= lines:
                    stopped = "lines"; break
            elif ev == "exit":
                code = int(msg.get("code", -1)); stopped = "exit"; break
    finally:
        ws.close()
        if logf:
            logf.close()
    ok = (code == 0) if stopped == "exit" else (stopped in ("duration", "lines", "until"))
    result = {"command": action, "name": name, "exit_code": code, "ok": ok,
              "stopped": stopped, "lines": count, "log_file": log_path}
    if mode == "summary":
        result["error_lines"] = errors[:30]
        result["tail"] = [l.rstrip("\n") for l in list(recent)[-tail:]]
    return result


def print_build_result(res, label):
    if _JSON:
        out(res)
        return
    if res["ok"]:
        print(f"[{label}] {res['name']}: OK ({res['lines']} lines)"
              + (f"  log={res['log_file']}" if res.get("log_file") else ""))
    else:
        if res.get("error_lines"):
            print("--- errors ---")
            for l in res["error_lines"]:
                print(l)
        if res.get("tail"):
            print("--- tail ---")
            for l in res["tail"]:
                print(l)
        print(f"[{label}] {res['name']}: FAILED (exit={res['exit_code']}, "
              f"stopped={res['stopped']})"
              + (f"  full log: {res['log_file']}" if res.get("log_file") else ""))


# --------------------------------------------------------------------------- #
# enumeration + capability cache
# --------------------------------------------------------------------------- #
CANDIDATE_ENDPOINTS = [
    ("/", "get"), ("/version", "get"), ("/devices", "get"), ("/info", "get"),
    ("/downloads", "get"), ("/download.bin", "get"), ("/secret_keys", "get"),
    ("/secret-keys", "get"), ("/json-config", "get"), ("/serial-ports", "get"),
    ("/boards", "get"), ("/login", "get"), ("/prometheus-sd", "get"),
    ("/ping", "get"), ("/edit", "get"),
    ("/wizard", "post"), ("/import", "post"), ("/ignore-device", "post"),
    ("/delete", "post"), ("/undo-delete", "post"), ("/archive", "post"),
    ("/unarchive", "post"),
    ("/logs", "ws"), ("/upload", "ws"), ("/run", "ws"), ("/compile", "ws"),
    ("/validate", "ws"), ("/clean", "ws"), ("/clean-mqtt", "ws"),
    ("/clean-all", "ws"), ("/update-all", "ws"), ("/rename", "ws"),
    ("/vscode", "ws"), ("/ace", "ws"), ("/events", "ws"),
]


def probe(conn):
    found = {}
    for path, kind in CANDIDATE_ENDPOINTS:
        s, h, _ = http_request(conn, "GET", path, timeout=10)
        present = s != 404 or _login_redirect(s, h)
        found[path] = {"kind": kind, "status": s, "present": present}
    return found


def save_caps(conn, version, endpoints):
    os.makedirs(os.path.dirname(CAPS_CACHE), exist_ok=True)
    data = {"builder": conn.origin, "esphome_version": version,
            "endpoints": endpoints, "saved": _dt.datetime.now().isoformat()}
    with open(CAPS_CACHE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return CAPS_CACHE


# --------------------------------------------------------------------------- #
# secrets
# --------------------------------------------------------------------------- #
def secret_keys(conn):
    for path in ("/secret_keys", "/secret-keys"):
        s, _, b = http_request(conn, "GET", path, timeout=10)
        if s == 404 or s >= 400:
            continue
        try:
            data = json.loads(b.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            data = [x.strip() for x in b.decode("utf-8", "replace").splitlines() if x.strip()]
        if isinstance(data, dict):
            return sorted(data.keys())   # names only — never values
        if isinstance(data, list):
            return sorted(str(x) for x in data)
        return []
    return None


def secret_refs(text):
    return sorted(set(SECRET_REF_RE.findall(text)))


def lint_yaml(text):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, _, value = s.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not value or value.startswith(("!secret", "!include", "!lambda", "${")):
            continue
        if any(h in key for h in SECRET_HINT_KEYS) and value not in ("true", "false", "none", "{}", "[]"):
            findings.append((i, line.rstrip(), f"sensitive key '{key}' has a literal value; use `!secret <name>`"))
    return findings


# --------------------------------------------------------------------------- #
# config CRUD
# --------------------------------------------------------------------------- #
def norm(name):
    return name if name.endswith(".yaml") else f"{name}.yaml"


def list_devices(conn):
    return get_json(conn, "/devices")


def configured_filenames(conn):
    data = list_devices(conn)
    items = data.get("configured", data) if isinstance(data, dict) else data
    names = []
    for d in items or []:
        names.append(d.get("configuration") or d.get("filename") or norm(d.get("name", "")))
    return [n for n in names if n]


def exists(conn, name):
    return norm(name) in set(configured_filenames(conn))


def get_config(conn, name):
    return get_text(conn, "/edit", params={"configuration": norm(name)})


def put_config(conn, name, text, *, allow_secrets=False):
    findings = lint_yaml(text)
    if findings and not allow_secrets:
        msg = ["refusing to save: config appears to contain inline secrets."]
        for ln, _, reason in findings:
            msg.append(f"  line {ln}: {reason}")
        msg.append("Move these into the Builder's secrets.yaml and reference with "
                   "`!secret <name>`, or pass --allow-secrets to override.")
        die("\n".join(msg))
    s, h, b = http_request(conn, "POST", "/edit", params={"configuration": norm(name)},
                           body=text, headers={"Content-Type": "text/plain; charset=utf-8"})
    if s == 401 or _login_redirect(s, h):
        die("authentication required to save config.")
    if s >= 400:
        die(f"save failed: HTTP {s}: {b[:300].decode('utf-8','replace')}")
    return True


def create_from_file(conn, name, text, *, force=False, allow_secrets=False):
    if not force and exists(conn, name):
        die(f"'{norm(name)}' already exists. Use --force to overwrite, or `put` to edit.")
    try:
        put_config(conn, name, text, allow_secrets=allow_secrets)
        return "edit"
    except SystemExit:
        # Some Builder versions won't create a brand-new file via /edit.
        # Fall back to wizard(empty) to create the file, then write content.
        create_via_wizard(conn, name, wtype="empty")
        put_config(conn, name, text, allow_secrets=allow_secrets)
        return "wizard+edit"


def create_via_wizard(conn, name, *, wtype="basic", platform=None, board=None,
                      ssid=None, psk=None, password=None):
    payload = {"name": name, "type": wtype}
    for k, v in (("platform", platform), ("board", board), ("ssid", ssid),
                 ("psk", psk), ("password", password)):
        if v:
            payload[k] = v
    s, h, b = http_request(conn, "POST", "/wizard", body=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
    if s == 409:
        die(f"a configuration named '{name}' already exists.")
    if s == 401 or _login_redirect(s, h):
        die("authentication required to create config.")
    if s >= 400:
        die(f"wizard failed: HTTP {s}: {b[:300].decode('utf-8','replace')}")
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {"configuration": norm(name)}


def delete_config(conn, name, undo=False):
    path = "/undo-delete" if undo else "/delete"
    cfg = norm(name)
    s, h, b = http_request(conn, "POST", path, params={"configuration": cfg})
    if s == 404 or s >= 400:
        s, h, b = http_request(conn, "POST", path, body=json.dumps({"configuration": cfg}),
                               headers={"Content-Type": "application/json"})
    if s == 401 or _login_redirect(s, h):
        die("authentication required to delete config.")
    if s >= 400:
        die(f"{path} failed: HTTP {s}: {b[:300].decode('utf-8','replace')}")
    return True


def _slug(name):
    base = name[:-5] if name.endswith(".yaml") else name
    s = "".join(c if (c.isalnum() or c == "-") else "-" for c in base.lower()).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s


def clone_config(conn, src, dst, *, new_name=None, friendly_name=None, allow_secrets=False, force=False):
    if not force and exists(conn, dst):
        die(f"'{norm(dst)}' already exists. Use --force to overwrite.")
    text = get_config(conn, src)
    src_slug = _slug(src)
    dst_slug = new_name or _slug(dst)
    new, changed = [], []
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        s = line.strip()
        o = line
        first = s.split()[0] if s else ""
        if first == "name:":
            ind = line[:len(line) - len(line.lstrip())]
            o = f"{ind}name: {dst_slug}\n"; changed.append((i, s, o.strip()))
        elif s.startswith("friendly_name:"):
            ind = line[:len(line) - len(line.lstrip())]
            fn = friendly_name or dst_slug.replace("-", " ").title()
            o = f"{ind}friendly_name: {fn}\n"; changed.append((i, s, o.strip()))
        elif src_slug and src_slug in s and s.startswith(("devicename:", "device_name:", "node_name:")):
            o = line.replace(src_slug, dst_slug); changed.append((i, s, o.strip()))
        new.append(o)
    put_config(conn, dst, "".join(new), allow_secrets=allow_secrets)
    return changed, "".join(new)


# --------------------------------------------------------------------------- #
# classification (fleet policy)
# --------------------------------------------------------------------------- #
def top_level_keys(text):
    keys = set()
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_]*):", line)
        if m:
            keys.add(m.group(1))
    return keys


def classify_text(text):
    """Return (cls, rationale, components). Decision order: sendspin > proxy > other."""
    keys = top_level_keys(text)
    is_sendspin = bool(SENDSPIN_RE.search(text))
    has_proxy = ("bluetooth_proxy" in keys) or ("esp32_ble_tracker" in keys)
    hardware = sorted(keys & HARDWARE_KEYS)
    if is_sendspin:
        return ("sendspin-player",
                "sendspin/resonate component present (experimental; pin to a "
                "known-good ESPHome version)", sorted(keys))
    if has_proxy:
        if hardware:
            return ("other",
                    f"has bluetooth_proxy/esp32_ble_tracker but also real hardware "
                    f"({', '.join(hardware)}); multi-role tiebreaker -> other", sorted(keys))
        return ("bluetooth-proxy",
                "dedicated bluetooth_proxy/esp32_ble_tracker, no physical I/O", sorted(keys))
    return ("other", f"no proxy/sendspin markers; components: {', '.join(sorted(keys)) or 'none'}",
            sorted(keys))


def classify_device(conn, name, deep=False):
    if deep:
        # /json-config expands packages/includes; more accurate but slower.
        try:
            cfg = get_json(conn, "/json-config", params={"configuration": norm(name)})
            text = json.dumps(cfg)  # flatten keys for scanning
            # rebuild a pseudo-top-level key set from the dict
            if isinstance(cfg, dict):
                pseudo = "\n".join(f"{k}:" for k in cfg.keys()) + "\n" + text
                cls, rat, comps = classify_text(pseudo)
                return {"name": norm(name), "class": cls, "rationale": rat + " [deep]",
                        "components": comps}
        except SystemExit:
            pass
    text = get_config(conn, name)
    cls, rat, comps = classify_text(text)
    return {"name": norm(name), "class": cls, "rationale": rat, "components": comps}


# --------------------------------------------------------------------------- #
# status / backup / watch
# --------------------------------------------------------------------------- #
def builder_version(conn):
    try:
        return get_text(conn, "/version").strip()
    except SystemExit:
        return ""


def online_map(conn):
    """filename -> bool, if the Builder exposes /ping; else {}."""
    s, _, b = http_request(conn, "GET", "/ping", timeout=10)
    if s >= 400:
        return {}
    try:
        data = json.loads(b.decode("utf-8", "replace"))
        return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def fleet_status(conn):
    data = list_devices(conn)
    items = data.get("configured", data) if isinstance(data, dict) else data
    current = builder_version(conn)
    pings = online_map(conn)
    rows = []
    for d in items or []:
        fn = d.get("configuration") or d.get("filename") or norm(d.get("name", ""))
        deployed = d.get("deployed_version") or d.get("current_version")
        online = pings.get(fn)
        rows.append({
            "name": d.get("name") or fn,
            "configuration": fn,
            "platform": d.get("target_platform") or d.get("platform"),
            "address": d.get("address"),
            "deployed_version": deployed,
            "update_available": bool(current and deployed and deployed != current),
            "online": online,
        })
    return {"builder_version": current, "devices": rows}


def backup_all(conn, out_dir, match=None):
    os.makedirs(out_dir, exist_ok=True)
    names = configured_filenames(conn)
    if match:
        names = [n for n in names if fnmatch.fnmatch(n, match) or fnmatch.fnmatch(n[:-5], match)]
    written, manifest = [], []
    for fn in names:
        text = get_config(conn, fn)
        path = os.path.join(out_dir, fn)
        os.makedirs(os.path.dirname(path) or out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
        manifest.append({"configuration": fn, "bytes": len(text),
                         "secret_refs": secret_refs(text)})
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump({"builder": conn.origin, "saved": _dt.datetime.now().isoformat(),
                   "files": manifest}, fh, indent=2)
    return written


def watch_device(conn, name, timeout=180):
    """Wait until `name` reports online via the /events socket. Returns bool."""
    import time as _t
    target = norm(name)
    target_stem = target[:-5]
    ws = WSClient(conn, "/events", timeout=timeout).connect()
    deadline = _t.monotonic() + timeout
    try:
        ws.send_json({"event": "refresh"})
        while _t.monotonic() < deadline:
            ws.set_timeout(max(0.2, deadline - _t.monotonic()))
            try:
                msg = ws.recv_json()
            except WSTimeout:
                break
            if msg is None:
                break
            ev = str(msg.get("event", "")).lower()
            data = msg.get("data", {}) or {}
            if "initial" in ev or "state" in ev and "ping" in data:
                pings = data.get("ping", {})
                if pings.get(target) or pings.get(target_stem):
                    return True
            if "state" in ev and "changed" in ev:
                fn = data.get("filename", "")
                if (fn == target or fn == target_stem or data.get("name") == target_stem) \
                        and data.get("state"):
                    return True
        return False
    finally:
        ws.close()


def expand_targets(conn, name, match):
    if match:
        names = [n for n in configured_filenames(conn)
                 if fnmatch.fnmatch(n, match) or fnmatch.fnmatch(n[:-5], match)]
        if not names:
            die(f"no configured devices match {match!r}")
        return names
    if not name:
        die("provide a device NAME or --match GLOB")
    return [norm(name)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_common(p):
    p.add_argument("--url")
    p.add_argument("--username")
    p.add_argument("--password")
    p.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed certs)")
    p.add_argument("--env-file", help="path to a .env file (default search: ./.env then ~/.config/esphome-builder/.env)")
    p.add_argument("--json", action="store_true", help="machine-parseable output")


def conn_from(a):
    load_env_files(getattr(a, "env_file", None))
    return Conn.load(a.url, a.username, a.password, True if a.insecure else None)


def _maybe_json(a):
    global _JSON
    if getattr(a, "json", False):
        _JSON = True


def build_parser():
    P = argparse.ArgumentParser(prog="esphome_dashboard.py",
                                description="Manage ESPHome devices and fleets via the ESPHome Builder.")
    sub = P.add_subparsers(dest="cmd", required=True)

    def cmd(name, help, args_fn=None):
        sp = sub.add_parser(name, help=help)
        add_common(sp)
        if args_fn:
            args_fn(sp)
        return sp

    cmd("connect", "verify connectivity/auth; --save to persist",
        lambda s: s.add_argument("--save", action="store_true"))
    cmd("enumerate", "probe + cache the Builder API surface",
        lambda s: s.add_argument("--save", action="store_true", help="cache capabilities to disk"))
    cmd("list", "list configured devices")
    cmd("status", "fleet health: online + update-available")
    cmd("info", "device metadata", lambda s: s.add_argument("name"))

    def get_args(s):
        s.add_argument("name"); s.add_argument("-o", "--out")
    cmd("get", "fetch a device YAML", get_args)

    def put_args(s):
        s.add_argument("name")
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("-f", "--file"); g.add_argument("--stdin", action="store_true")
        s.add_argument("--allow-secrets", action="store_true")
    cmd("put", "save a device YAML (lints secrets)", put_args)

    def create_args(s):
        s.add_argument("name")
        s.add_argument("--from-file", help="create from a prepared YAML (recommended)")
        s.add_argument("--type", default="basic", choices=["basic", "empty"])
        s.add_argument("--platform"); s.add_argument("--board")
        s.add_argument("--force", action="store_true")
        s.add_argument("--allow-secrets", action="store_true")
    cmd("create", "create a new device", create_args)

    def clone_args(s):
        s.add_argument("src"); s.add_argument("dst")
        s.add_argument("--name"); s.add_argument("--friendly-name")
        s.add_argument("--force", action="store_true")
        s.add_argument("--allow-secrets", action="store_true")
    cmd("clone", "clone a device (minimal identity edits)", clone_args)

    def del_args(s):
        s.add_argument("name"); s.add_argument("--undo", action="store_true")
    cmd("delete", "delete/restore a device", del_args)

    def rename_args(s):
        s.add_argument("name"); s.add_argument("new_name")
    cmd("rename", "rename a device", rename_args)

    def target_args(s):
        s.add_argument("name", nargs="?")
        s.add_argument("--match", help="glob over configured devices (batch)")
    cmd("validate", "validate config(s)", target_args)
    cmd("compile", "compile firmware on the Builder", target_args)
    cmd("clean", "clean build files", target_args)

    def deploy_args(s):
        target_args(s)
        s.add_argument("--port", default="OTA", help="OTA (default), an IP, or /dev/ttyUSB0")
        s.add_argument("--verbose", action="store_true", help="stream full output")
    cmd("upload", "compile + upload via the Builder", deploy_args)

    def logs_args(s):
        s.add_argument("name")
        s.add_argument("--port", default="OTA")
        s.add_argument("--duration", type=int, help="stop after N seconds")
        s.add_argument("--lines", type=int, help="stop after N lines")
        s.add_argument("--until", help="stop when a line matches this regex")
        s.add_argument("--follow", action="store_true", help="unbounded (use with care)")
    cmd("logs", "stream device logs (bounded by default)", logs_args)

    def run_args(s):
        logs_args(s)
        s.add_argument("--verbose", action="store_true")
    cmd("run", "compile + upload + logs (prefer compile/upload/logs for agents)", run_args)

    cmd("update-all", "update every device to the current ESPHome version")

    def classify_args(s):
        s.add_argument("name", nargs="?")
        s.add_argument("--all", action="store_true")
        s.add_argument("--file", help="classify a local YAML file")
        s.add_argument("--deep", action="store_true", help="expand packages via /json-config")
    cmd("classify", "assign device class(es): bluetooth-proxy/sendspin-player/other", classify_args)

    def secrets_args(s):
        s.add_argument("--check", metavar="NAME", help="report !secret refs missing from the Builder")
        s.add_argument("--file", help="check a local YAML instead of a Builder config")
    cmd("secrets", "list secret KEY NAMES (never values); --check a config", secrets_args)

    def backup_args(s):
        s.add_argument("--out", default=os.environ.get("ESPHOME_BACKUP_DIR", "./esphome-backup"))
        s.add_argument("--match")
    cmd("backup", "save all/matched device YAMLs to a directory (git-friendly)", backup_args)

    def watch_args(s):
        s.add_argument("name"); s.add_argument("--timeout", type=int, default=180)
    cmd("watch", "wait for a device to come online (post-OTA)", watch_args)

    def lint_args(s):
        s.add_argument("-f", "--file", required=True)
    sp = sub.add_parser("lint", help="flag inline secrets in a local YAML")
    sp.add_argument("--json", action="store_true"); lint_args(sp)

    cmd("downloads", "list downloadable binaries", lambda s: s.add_argument("name"))
    return P


def run_target_batch(conn, a, ws_path, label, mode="summary", **stream_kw):
    targets = expand_targets(conn, a.name, getattr(a, "match", None))
    results = []
    for fn in targets:
        if len(targets) > 1:
            progress(f"== {label} {fn} ({len(results)+1}/{len(targets)}) ==")
        spawn = {"configuration": fn}
        if "port" in stream_kw:
            spawn["port"] = stream_kw.pop("port")
        res = stream_command(conn, ws_path, spawn, mode=mode, action=label, name=fn,
                             **{k: v for k, v in stream_kw.items() if k in
                                ("tail", "log_path", "duration", "lines", "until")})
        results.append(res)
        if mode == "summary":
            print_build_result(res, label)
    if _JSON:
        out({"command": label, "results": results})
    fails = [r for r in results if not r["ok"]]
    return 0 if not fails else 1


def main(argv=None):
    a = build_parser().parse_args(argv)
    _maybe_json(a)

    if a.cmd == "lint":
        with open(a.file, encoding="utf-8") as fh:
            findings = lint_yaml(fh.read())
        if not findings:
            out({"ok": True, "findings": []}, "ok: no inline secrets detected.")
            return 0
        out({"ok": False, "findings": [{"line": ln, "reason": r} for ln, _, r in findings]})
        if not _JSON:
            for ln, line, r in findings:
                print(f"line {ln}: {r}\n    {line}")
        return 1

    if a.cmd == "classify" and getattr(a, "file", None):
        with open(a.file, encoding="utf-8") as fh:
            cls, rat, comps = classify_text(fh.read())
        r = {"name": a.file, "class": cls, "rationale": rat, "components": comps}
        out(r, f"{a.file}: {cls}\n  {rat}")
        return 0

    conn = conn_from(a)

    if a.cmd == "connect":
        v = builder_version(conn)
        out({"ok": True, "builder": conn.origin, "esphome_version": v},
            f"connected to {conn.origin}\nesphome version: {v or '(unknown)'}")
        if a.save:
            p = conn.save(a.env_file)
            out(None, f"saved connection variables to {p}")
        return 0

    if a.cmd == "enumerate":
        v = builder_version(conn)
        eps = probe(conn)
        result = {"builder": conn.origin, "esphome_version": v, "endpoints": eps}
        if a.save:
            result["cache"] = save_caps(conn, v, eps)
        out(result, json.dumps(result, indent=2, sort_keys=True))
        return 0

    if a.cmd == "list":
        out(list_devices(conn), json.dumps(list_devices(conn), indent=2))
        return 0

    if a.cmd == "status":
        st = fleet_status(conn)
        if _JSON:
            out(st); return 0
        print(f"builder esphome version: {st['builder_version'] or '(unknown)'}")
        for d in st["devices"]:
            flags = []
            if d["update_available"]:
                flags.append("UPDATE")
            if d["online"] is False:
                flags.append("offline")
            elif d["online"] is True:
                flags.append("online")
            print(f"  {d['configuration']:<32} {str(d['deployed_version'] or '?'):<12} "
                  f"{d['platform'] or '':<8} {' '.join(flags)}")
        return 0

    if a.cmd == "info":
        out(get_json(conn, "/info", params={"configuration": norm(a.name)}),
            json.dumps(get_json(conn, "/info", params={"configuration": norm(a.name)}), indent=2))
        return 0

    if a.cmd == "get":
        text = get_config(conn, a.name)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            out({"ok": True, "wrote": a.out, "bytes": len(text)}, f"wrote {a.out} ({len(text)} bytes)")
        else:
            sys.stdout.write(text)
        return 0

    if a.cmd == "put":
        text = sys.stdin.read() if a.stdin else open(a.file, encoding="utf-8").read()
        put_config(conn, a.name, text, allow_secrets=a.allow_secrets)
        out({"ok": True, "saved": norm(a.name)}, f"saved {norm(a.name)}")
        return 0

    if a.cmd == "create":
        if a.from_file:
            text = open(a.from_file, encoding="utf-8").read()
            how = create_from_file(conn, a.name, text, force=a.force, allow_secrets=a.allow_secrets)
            out({"ok": True, "created": norm(a.name), "method": how},
                f"created {norm(a.name)} from {a.from_file} (via {how})")
        else:
            res = create_via_wizard(conn, a.name, wtype=a.type, platform=a.platform, board=a.board)
            cfg = res.get("configuration", norm(a.name))
            out({"ok": True, "created": cfg, "method": "wizard"}, f"created {cfg}")
            if a.type == "basic" and not _JSON:
                print("note: the wizard may write a generated api key / ota password inline. "
                      "Run `secrets --check` / `lint` and migrate them into secrets.yaml.")
        return 0

    if a.cmd == "clone":
        changed, _ = clone_config(conn, a.src, a.dst, new_name=a.name,
                                  friendly_name=a.friendly_name, force=a.force,
                                  allow_secrets=a.allow_secrets)
        out({"ok": True, "src": norm(a.src), "dst": norm(a.dst),
             "changed": [{"line": ln, "from": b, "to": t} for ln, b, t in changed]},
            None)
        if not _JSON:
            print(f"cloned {norm(a.src)} -> {norm(a.dst)}")
            for ln, b, t in changed:
                print(f"  line {ln}: {b}  ->  {t}")
            print("reminder: API encryption keys and OTA passwords must be UNIQUE per "
                  "device. Add fresh per-device keys to the Builder's secrets.yaml and "
                  "point this clone at them, then validate -> compile -> upload.")
        return 0

    if a.cmd == "delete":
        delete_config(conn, a.name, undo=a.undo)
        out({"ok": True, "name": norm(a.name), "undo": a.undo},
            f"{'restored' if a.undo else 'deleted'} {norm(a.name)}")
        return 0

    if a.cmd == "rename":
        res = stream_command(conn, "/rename",
                             {"configuration": norm(a.name), "newName": a.new_name},
                             mode="summary", action="rename", name=norm(a.name))
        print_build_result(res, "rename")
        return 0 if res["ok"] else 1

    if a.cmd in ("validate", "compile", "clean"):
        ws = {"validate": "/validate", "compile": "/compile", "clean": "/clean"}[a.cmd]
        return run_target_batch(conn, a, ws, a.cmd, mode="summary")

    if a.cmd == "upload":
        mode = "stream" if a.verbose else "summary"
        return run_target_batch(conn, a, "/upload", "upload", mode=mode, port=a.port)

    if a.cmd in ("logs", "run"):
        ws = {"logs": "/logs", "run": "/run"}[a.cmd]
        duration = None if a.follow else a.duration
        lines = None if a.follow else a.lines
        if a.cmd == "logs" and not a.follow and not duration and not lines and not a.until:
            duration, lines = 60, 80  # safe default bound for agents
        mode = "stream"
        if a.cmd == "run" and getattr(a, "verbose", False):
            mode = "stream"
        res = stream_command(conn, ws, {"configuration": norm(a.name), "port": a.port},
                             mode=mode, action=a.cmd, name=norm(a.name),
                             duration=duration, lines=lines, until=a.until)
        if _JSON:
            out(res)
        else:
            print(f"\n[{a.cmd}] {norm(a.name)}: stopped={res['stopped']} "
                  f"exit={res['exit_code']} lines={res['lines']}")
        return 0 if res["ok"] else 1

    if a.cmd == "update-all":
        res = stream_command(conn, "/update-all", {}, mode="summary",
                             action="update-all", name="fleet")
        print_build_result(res, "update-all")
        return 0 if res["ok"] else 1

    if a.cmd == "classify":
        if a.file:
            with open(a.file, encoding="utf-8") as fh:
                cls, rat, comps = classify_text(fh.read())
            r = {"name": a.file, "class": cls, "rationale": rat, "components": comps}
            out(r, f"{a.file}: {cls}\n  {rat}")
            return 0
        if a.all:
            results = [classify_device(conn, fn, deep=a.deep) for fn in configured_filenames(conn)]
            if _JSON:
                out({"devices": results})
            else:
                for r in results:
                    print(f"  {r['name']:<32} {r['class']:<16} {r['rationale']}")
            return 0
        if not a.name:
            die("provide a NAME, --all, or --file")
        r = classify_device(conn, a.name, deep=a.deep)
        out(r, f"{r['name']}: {r['class']}\n  {r['rationale']}")
        return 0

    if a.cmd == "secrets":
        if a.file or a.check:
            text = open(a.file, encoding="utf-8").read() if a.file else get_config(conn, a.check)
            refs = secret_refs(text)
            available = set(secret_keys(conn) or [])
            missing = [r for r in refs if r not in available]
            res = {"referenced": refs, "missing": missing,
                   "ok": not missing}
            if _JSON:
                out(res)
            else:
                print(f"!secret refs: {', '.join(refs) or '(none)'}")
                if missing:
                    print(f"MISSING from Builder secrets.yaml: {', '.join(missing)}")
                    print("Add these keys (unique per device where required) before compiling. "
                          "Do not inline the values.")
                else:
                    print("ok: all referenced secret keys exist in the Builder.")
            return 0 if not missing else 1
        keys = secret_keys(conn)
        if keys is None:
            die("no secret-keys endpoint on this Builder version.")
        out({"secret_keys": keys},
            "# secret KEY NAMES (values never exposed)\n" + "\n".join(keys))
        return 0

    if a.cmd == "backup":
        written = backup_all(conn, a.out, match=a.match)
        out({"ok": True, "out": a.out, "files": written},
            f"backed up {len(written)} config(s) to {a.out}")
        return 0

    if a.cmd == "watch":
        ok = watch_device(conn, a.name, timeout=a.timeout)
        out({"ok": ok, "name": norm(a.name), "online": ok},
            f"{norm(a.name)} is {'online' if ok else 'NOT online within timeout'}")
        return 0 if ok else 1

    if a.cmd == "downloads":
        d = get_json(conn, "/downloads", params={"configuration": norm(a.name)})
        out(d, json.dumps(d, indent=2))
        return 0

    build_parser().error(f"unhandled command {a.cmd}")


if __name__ == "__main__":
    sys.exit(main())
