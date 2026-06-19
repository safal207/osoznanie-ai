from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from osoznanie.playwright_runner import (
    BrowserCheckCode,
    PlaywrightBrowserCheckRunner,
    PlaywrightCheckInput,
)


class CheckoutHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        fixed = self.path.startswith("/fixed")
        click = (
            "document.getElementById('confirmation').style.display='block'"
            if fixed
            else "void(0)"
        )
        body = f"""
        <!doctype html>
        <html><body>
          <button id="checkout" onclick="{click}">Checkout</button>
          <div id="confirmation" style="display:none">Order created</div>
        </body></html>
        """.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


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
        thread.join(timeout=2)


def browser_request(url: str) -> PlaywrightCheckInput:
    return PlaywrightCheckInput(
        release_id="release-browser",
        target_url=url,
        action_selector="#checkout",
        success_selector="#confirmation",
        changed_components=["checkout-button"],
        timeout_ms=1_500,
    )


@pytest.mark.playwright
def test_real_chromium_detects_bug_and_accepts_fixed_page() -> None:
    runner = PlaywrightBrowserCheckRunner()
    with checkout_server() as base_url:
        buggy = runner.run(browser_request(f"{base_url}/buggy?secret=hidden"))
        fixed = runner.run(browser_request(f"{base_url}/fixed"))

    assert buggy.code is BrowserCheckCode.EXPECTED_STATE_NOT_OBSERVED
    assert buggy.passed is False
    assert "secret" not in buggy.target
    assert fixed.code is BrowserCheckCode.PASSED
    assert fixed.passed is True
