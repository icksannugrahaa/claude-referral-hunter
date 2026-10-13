"""Minimal receiver for the generic webhook notifier (stdlib only).

    WEBHOOK_SECRET=change-me python examples/webhook_receiver.py
    # hunter side: WEBHOOK_URL=http://127.0.0.1:8080/referral WEBHOOK_SECRET=change-me

Replace forward() with your bot's send call (WhatsApp, LINE, Slack, ...).
"""

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()


def forward(finding: dict) -> None:
    print(f"[new] {finding['code']} from {finding['source']} {finding.get('author', '')}: {finding['url']}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if SECRET:
            expected = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get("X-Signature-256", "")):
                self.send_response(401)
                self.end_headers()
                return
        body = json.loads(raw)
        for finding in body.get("findings", []):
            forward(finding)
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
