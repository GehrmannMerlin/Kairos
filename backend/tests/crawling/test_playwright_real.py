"""真实 Playwright Chromium 渲染验证（浏览器二进制存在时执行）。

不是 Kairos 前端 Browser E2E；只验证 M-10 BrowserRender Activity 的真实渲染路径
（本地 fixture 服务器）。chromium 不可用时跳过并如实披露。
"""

from __future__ import annotations

import glob
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

_JS_PAGE = b"""<html><head></head><body><div id="app">initial</div>
<script>document.getElementById("app").textContent = "JS_RENDERED_MARKER_42";</script>
</body></html>"""


def _chromium_available() -> bool:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~")) + os.sep + "ms-playwright"
    patterns = [
        os.path.join(base, "chromium-*", "*", "chrome.exe"),
        os.path.join(base, "chromium-*", "*", "headless_shell.exe"),
    ]
    return any(glob.glob(p) for p in patterns)


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="playwright chromium 未安装，跳过真实浏览器验证"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = _JS_PAGE if self.path == "/" else b"not found"
        self.send_response(200 if self.path == "/" else 404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ARG002
        pass


@pytest.fixture(scope="module")
def js_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    yield url
    server.shutdown()


@pytest.mark.asyncio
async def test_real_chromium_renders_js(js_server) -> None:
    from app.crawling.browser import PlaywrightChromiumRenderer

    renderer = PlaywrightChromiumRenderer(headless=True, allow_hosts=frozenset({"127.0.0.1"}))
    page = await renderer.render(url=js_server)
    html = page.html.decode("utf-8", errors="ignore")
    assert "JS_RENDERED_MARKER_42" in html  # JS 注入内容被真实 Chromium 渲染
