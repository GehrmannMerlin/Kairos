"""M-10 executor 绑定无栈验证：install_fetch_executors 注册 FETCH + BROWSER_RENDER。"""
from __future__ import annotations

from app.crawling.executors import install_fetch_executors
from app.plan.executors import is_registered
from app.plan.nodes import NodeType


def test_install_fetch_executors_registers_fetch_and_browser() -> None:
    install_fetch_executors()
    assert is_registered(NodeType.FETCH)
    assert is_registered(NodeType.BROWSER_RENDER)


def test_no_http_playwright_fetch_plan_node_leak() -> None:
    """Agent Plan 只看到 Fetch / BrowserRender；不出现 HttpFetch/ScrapyFetch/PlaywrightFetch。"""
    from app.plan.nodes import NodeType

    names = {n.value for n in NodeType}
    assert "fetch" in names and "browser_render" in names
    assert not ({"http_fetch", "scrapy_fetch", "playwright_fetch"} & names)
