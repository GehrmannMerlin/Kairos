"""TEST G：compose 网络契约 + production 模板不引用 staging。

- 内部服务零 host 端口发布（公网只由共享 reverse proxy 暴露 80/443）。
- production 模板 Secret 全部 ${VAR:?} 必填、无默认值。
- production 不引用 staging DB/bucket/namespace。
- staging CORS 只允许 staging 域名；production CORS 只允许正式域名。
"""

from __future__ import annotations

import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


def _compose_file(name: str) -> str:
    path = os.path.join(ROOT, "infra", "compose", name)
    assert os.path.exists(path), f"missing {path}"
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _host_ports(text: str) -> list[str]:
    """匹配 ports: 块里的 host 端口发布（"8000:80" / "${VAR}:80"），不含注释与 localhost 专用行。"""
    # 只匹配出现在 services: 下的端口映射（冒号后跟容器端口）
    return re.findall(r'^\s+-\s*["\']?[0-9A-Z_${}:]+:[0-9]{2,5}', text, re.M)


def test_base_internal_services_do_not_publish_host_ports() -> None:
    base = _compose_file("compose.base.yml")
    assert _host_ports(base) == [], f"unexpected host ports in base: {_host_ports(base)}"


def test_production_internal_services_do_not_publish_host_ports() -> None:
    prod = _compose_file("compose.production.yml")
    assert _host_ports(prod) == [], f"unexpected host ports in production: {_host_ports(prod)}"


def test_production_template_secrets_have_no_defaults() -> None:
    prod = _compose_file("compose.production.yml")
    required = (
        "POSTGRES_PASSWORD",
        "MINIO_SECRET_KEY",
        "KAIROS_CREDENTIAL_MASTER_KEY",
        "KAIROS_API_IMAGE",
    )
    for var in required:
        assert f"${{{var}:?}}" in prod, f"{var} must be required (${var}:?)"


def test_production_does_not_reference_staging() -> None:
    prod = _compose_file("compose.production.yml")
    assert "kairos-staging" not in prod
    assert "-dev" not in prod
    assert "kairos-dev" not in prod


def test_production_cors_is_real_origin_not_dev() -> None:
    prod = _compose_file("compose.production.yml")
    cors_line = [ln for ln in prod.splitlines() if "KAIROS_CORS_ORIGINS" in ln][0]
    assert '["https://app.kairos.ac.cn"]' in cors_line
    assert "localhost" not in cors_line
    assert "*" not in cors_line


def test_staging_cors_is_staging_only() -> None:
    base = _compose_file("compose.base.yml")
    staging = _compose_file("compose.staging.yml")
    combined = base + "\n" + staging
    assert '["https://staging.kairos.ac.cn"]' in combined
    assert '["*"]' not in combined


def test_production_vhost_proxies_to_production_containers_only() -> None:
    # M-18：production vhost 必须代理到 kairos-production-*-1 容器，
    # 不能命中 staging 独占的 kairos-api/kairos-web 容器名（否则串环境）。
    vhost_path = os.path.join(ROOT, "infra", "reverse-proxy", "zz-kairos-production-tls.conf")
    assert os.path.exists(vhost_path), f"missing {vhost_path}"
    with open(vhost_path, encoding="utf-8") as fh:
        vhost = fh.read()
    assert "kairos-production-api-1:8000" in vhost
    assert "kairos-production-web-1:80" in vhost
    assert "http://kairos-api:8000" not in vhost, "must not proxy to staging api"
    assert "http://kairos-web:80" not in vhost, "must not proxy to staging web"
