"""Local deterministic checkout pages for the Playwright memory benchmark."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class BenchmarkCheckoutHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        buggy = self.path.startswith("/buggy")
        click = (
            "void(0)"
            if buggy
            else "document.getElementById('confirmation').style.display='block'"
        )
        body = f"""
        <!doctype html>
        <html><body>
          <main data-ground-truth="{'buggy' if buggy else 'healthy'}">
            <button id="checkout" onclick="{click}">Checkout</button>
            <div id="confirmation" style="display:none">Order created</div>
          </main>
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
def benchmark_checkout_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BenchmarkCheckoutHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
