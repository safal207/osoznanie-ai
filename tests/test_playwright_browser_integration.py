from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from osoznanie.playwright_runner import (
    BrowserCheckCode,
    PlaywrightBrowserCheckRunner,
    PlaywrightCheckInput,
)

pytest.importorskip("playwright.sync_api")


class CheckoutHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        broken = self.path.startswith("/broken")
        onclick = "" if broken else "document.querySelector('#confirmation').style.display='block'"
        body = f"""
        <!doctype html>
        <html>
          <body>
            <button id='checkout' onclick=\"{onclick}\">Checkout</button>
            <div id='confirmation' style='display:none'>Order confirmed</div>
          </body>
        </html>
        """
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        del format, args


@contextmanager
def checkout_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(url: str) -> PlaywrightCheckInput:
    return PlaywrightCheckInput(
        release_id="release-browser",
        target_url=url,
        action_selector="#checkout",
        success_selector="#confirmation",
        changed_components=["checkout-button"],
        timeout_ms=2_000,
    )


def test_real_chromium_detects_working_and_broken_checkout() -> None:
    runner = PlaywrightBrowserCheckRunner()
    with checkout_server() as base_url:
        working = runner.run(request(f"{base_url}/working"))
        broken = runner.run(request(f"{base_url}/broken"))

    assert working.code is BrowserCheckCode.PASSED
    assert working.passed is True
    assert broken.code is BrowserCheckCode.EXPECTED_STATE_NOT_OBSERVED
    assert broken.passed is False
