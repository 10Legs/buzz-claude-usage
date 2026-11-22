#!/usr/bin/env python3
"""claude_usage_server — read-only JSON endpoint for the Claude usage plugin.

  CLAUDE_USAGE_CONFIG=config.json python3 server/claude_usage_server.py

Listens on 127.0.0.1:8792 by default. Put HTTPS in front of it (for example Tailscale Serve).
Every request needs `Authorization: Bearer <token>`.

  GET /usage?days=N   usage report JSON for the last N days (1..max_days), cached for cache_seconds
  GET /healthz        "ok" (no token needed)

Standard library only. It reads ~/.claude/projects and never writes anything.
"""
import hmac, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_usage_report import load_config, report  # noqa: E402


def load_token(cfg):
    tok = os.environ.get("CLAUDE_USAGE_TOKEN")
    if not tok:
        path = os.path.join(cfg["_config_dir"], cfg["token_file"])
        try:
            with open(path) as f:
                tok = f.read().strip()
        except OSError:
            sys.exit(f"no token: create {path} (openssl rand -hex 24 > token) or set CLAUDE_USAGE_TOKEN")
    if len(tok) < 24:
        sys.exit("token must be at least 24 characters")
    return tok


def make_handler(cfg, token):
    cache, lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "claude-usage"
        sys_version = ""

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, code, msg):
            self._send(code, json.dumps({"error": msg}))

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self._send(200, "ok", "text/plain")
            auth = self.headers.get("authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                return self._error(401, "missing or wrong token")
            if u.path != "/usage":
                return self._error(404, "not found")
            try:
                days = int(parse_qs(u.query).get("days", ["7"])[0])
            except ValueError:
                return self._error(400, "days must be a whole number")
            days = max(1, min(int(cfg["max_days"]), days))
            with lock:
                hit = cache.get(days)
                if not hit or time.time() - hit[0] > cfg["cache_seconds"]:
                    try:
                        hit = cache[days] = (time.time(), json.dumps(report(cfg, days)).encode())
                    except Exception as e:  # report errors shouldn't kill the server
                        print(f"report failed: {e!r}", file=sys.stderr, flush=True)
                        return self._error(500, "report failed; see server log")
            self._send(200, hit[1])

        def log_message(self, fmt, *args):  # quiet: no per-request logs, never the token
            pass

    return Handler


def main():
    cfg = load_config()
    if not os.path.isdir(cfg["projects_dir"]):
        sys.exit(f"projects_dir not found: {cfg['projects_dir']}")
    token = load_token(cfg)
    srv = ThreadingHTTPServer((cfg["bind_host"], int(cfg["port"])), make_handler(cfg, token))
    print(f"claude-usage listening on http://{cfg['bind_host']}:{cfg['port']} (reading {cfg['projects_dir']})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
