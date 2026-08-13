"""M-10 crawling 测试共享 fixture（FakeTransport / FakeStorage / FakeRenderer / 内存 SQLite）。"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.auth.repository import UserRepository
from app.crawling.http_fetch import SafeFetchHttp
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import DiscoverySource, FrontierState
from app.discovery.robots import RobotsCache
from app.domain.repository import RunRepository, SpecVersionRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SITE_HOST = "fixture.test"


class _FakeRaw:
    """同时满足 DiscoveryHttp(.text) 与 SafeFetchHttp(.content) 的响应对象。"""

    def __init__(self, status_code: int, headers: dict, content: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.text = content.decode("utf-8", errors="ignore")


class FakeFetchTransport:
    """按 path 返回固定站点内容；route 可为 dict 或 callable(headers)->_FakeRaw。"""

    def __init__(self, routes: dict | None = None) -> None:
        self._routes = routes or {}
        self.requests: list[tuple[str, str, dict | None]] = []

    async def request(
        self, *, method: str, url: str, timeout_seconds: float, headers: dict | None = None
    ):
        self.requests.append((method, url, headers))
        path = urlsplit(url).path
        entry = self._routes.get(path)
        if callable(entry):
            result = entry(headers or {})
            if isinstance(result, dict):
                result_headers = {"content-type": result.get("content_type", "text/html")}
                result_headers.update(result.get("headers") or {})
                return _FakeRaw(result.get("status", 200), result_headers, result.get("body", b""))
            return result
        if entry is None:
            return _FakeRaw(404, {"content-type": "text/html"}, b"")
        resp_headers = {"content-type": entry.get("content_type", "text/html")}
        resp_headers.update(entry.get("headers") or {})
        return _FakeRaw(
            entry.get("status", 200),
            resp_headers,
            entry.get("body", b""),
        )


def default_routes() -> dict:
    return {
        "/robots.txt": {
            "status": 200,
            "content_type": "text/plain",
            "body": b"User-agent: *\nAllow: /\n",
        },
        "/": {
            "status": 200,
            "body": b"<html><body><p>Hello Static World</p></body></html>",
        },
        "/dynamic": {
            "status": 200,
            "body": (
                b'<html><head><script src="/app.js"></script></head>'
                b'<body><div id="app"></div></body></html>'
            ),
        },
        "/captcha": {
            "status": 200,
            "body": b"<html><body>please solve the captcha to continue</body></html>",
        },
        "/private": {"status": 401, "body": b"unauthorized"},
    }


class FakeRenderer:
    """Fake Playwright renderer：返回 RenderedPage，记录调用次数。"""

    def __init__(
        self, rendered_html: bytes = b"<html><body><p>Rendered Dynamic</p></body></html>"
    ) -> None:
        from app.crawling.browser import RenderedPage

        self.rendered = RenderedPage(html=rendered_html, final_url="http://fixture.test/dynamic")
        self.invocation_count = 0
        self.last_url: str | None = None

    async def render(
        self, *, url: str, timeout_seconds: float = 60.0, cookies: list[dict] | None = None
    ):
        self.invocation_count += 1
        self.last_url = url
        return self.rendered


class FakeStorage:
    """内存 ObjectStorage：记录 put 次数与已存在 key，便于断言 Blob 复用。"""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.put_calls = 0

    async def ensure_bucket(self) -> None:
        pass

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        self.put_calls += 1
        self._objects[key] = data
        return None

    async def get(self, key: str) -> bytes:
        return self._objects[key]

    async def exists(self, key: str) -> bool:
        return key in self._objects

    async def head(self, key: str):
        if key not in self._objects:
            return None
        return None


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def renderer() -> FakeRenderer:
    return FakeRenderer()


@pytest.fixture()
def fake_transport() -> FakeFetchTransport:
    return FakeFetchTransport(default_routes())


@pytest.fixture()
def http(fake_transport) -> SafeFetchHttp:
    return SafeFetchHttp(
        transport=fake_transport, allow_hosts=frozenset({SITE_HOST}), max_bytes=5_000_000
    )


@pytest.fixture()
def robots(fake_transport) -> RobotsCache:
    return RobotsCache(
        DiscoveryHttp(transport=fake_transport, allow_hosts=frozenset({SITE_HOST})),
        user_agent="KairosBot",
    )


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("crawling@example.com", "hash", None)
    task = TaskRepository(db).create(
        user_id=user.id, title="M-10 crawling", task_type="SPECIFIED_SOURCE"
    )
    run = RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )
    SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=spec_payload(),
    )
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def seed_ready(ctx, url: str, spec: dict | None = None) -> str:
    """把 URL 放入 Frontier 并置为 READY_FOR_FETCH（M-09 → M-10 handoff 输入）。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    if spec is not None:
        from app.domain.models import CollectionSpecVersion

        existing = (
            db.query(CollectionSpecVersion)
            .filter(
                CollectionSpecVersion.user_id == user.id,
                CollectionSpecVersion.task_id == task.id,
                CollectionSpecVersion.version == 1,
            )
            .first()
        )
        if existing is not None:
            existing.payload = spec
            db.commit()
        else:
            SpecVersionRepository(db).create(
                user_id=user.id,
                task_id=task.id,
                version=1,
                spec_type="collection",
                schema_version="m06.1",
                payload=spec,
            )
    frontier = UrlFrontierRepository(db)
    url_hash, _ = frontier.upsert_discovery(
        task_id=task.id,
        user_id=user.id,
        run_id=run.id,
        spec_version=1,
        raw_url=url,
        source=DiscoverySource.USER_SEED,
    )
    frontier.mark_state(
        user_id=user.id, task_id=task.id, url_hash=url_hash, state=FrontierState.READY_FOR_FETCH
    )
    return url_hash


def spec_payload(task_type: str = "SPECIFIED_SOURCE", seed_urls: list[str] | None = None) -> dict:
    return {
        "task_type": task_type,
        "goal": "m10 fetch",
        "fields": [{"name": "标题", "type": "text", "required": True}],
        "source_scope": {
            "mode": task_type,
            "seed_urls": seed_urls or [f"http://{SITE_HOST}/"],
            "source_hints": [],
        },
        "completion_conditions": [{"kind": "min_records", "target": 1}],
        "advanced_settings": {},
    }


def make_unit(run, index: int, node_type: str, parameters: dict | None = None) -> ExecutionUnit:
    return ExecutionUnit(
        run_id=run.id,
        index=index,
        unit_type=node_type,
        input_fingerprint=f"fp-{index}",
        node_id=f"n{index}",
        node_type=node_type,
        definition_version="1.0.0",
        parameters=parameters or {},
    )
