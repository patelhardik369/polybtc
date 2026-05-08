#!/usr/bin/env python3
"""
poly_proxy.py — local backend for the BTC dashboard.

Serves index.html plus a /poly/slug/<slug> endpoint that fetches the requested
Polymarket market server-side. Browsers can't read gamma-api directly because
gamma only sets Access-Control-Allow-Origin for https://polymarket.com, but
Python has no such restriction.

    python poly_proxy.py
    # then open http://localhost:8088

Stdlib only — no pip install.
"""
import http.server
import json
import socketserver
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PORT = 8088
ROOT = Path(__file__).parent
GAMMA = "https://gamma-api.polymarket.com/markets"
GAMMA_TIMEOUT = 5  # seconds


def parse_iso_ms(s: str) -> int:
    """Parse an ISO-8601 timestamp into Unix milliseconds (UTC)."""
    try:
        dt = datetime.fromisoformat(s)  # 3.11+ handles trailing Z
    except ValueError:
        dt = datetime.fromisoformat(s.rstrip("Z") + "+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _maybe_parse(v):
    return json.loads(v) if isinstance(v, str) else v


def fetch_gamma_market(slug: str):
    url = f"{GAMMA}?slug={urllib.parse.quote(slug)}"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=GAMMA_TIMEOUT) as r:
        data = json.loads(r.read().decode())
    if not data:
        return None
    m = data[0] if isinstance(data, list) else data
    if not m.get("clobTokenIds") or not m.get("endDate"):
        return None
    return {
        "slug": m["slug"],
        "question": m.get("question"),
        "tokenIds": _maybe_parse(m["clobTokenIds"]),
        "outcomes": _maybe_parse(m.get("outcomes", "[]")),
        "endMs": parse_iso_ms(m["endDate"]),
        "startMs": parse_iso_ms(m["startDate"]),
        "initialPrices": _maybe_parse(m.get("outcomePrices", "[]")),
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _send_json(self, status: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/poly/slug/"):
            slug = urllib.parse.unquote(self.path[len("/poly/slug/"):].split("?", 1)[0])
            try:
                m = fetch_gamma_market(slug)
            except urllib.error.HTTPError as e:
                self._send_json(502, {"error": f"gamma HTTP {e.code}"})
                return
            except urllib.error.URLError as e:
                self._send_json(502, {"error": f"gamma network: {e.reason}"})
                return
            except Exception as e:
                self._send_json(502, {"error": str(e)})
                return
            if m is None:
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, m)
            return
        return super().do_GET()


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    with ReusableServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"BTC dashboard → http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
