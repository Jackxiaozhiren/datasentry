"""D3-02：测试套件隐式依赖开发机代理环境——session 级 autouse fixture。

远端调度器默认 trust_env=True（httpx 默认），CI/公司网络下未设置 NO_PROXY
时回环请求会被代理拦截为 HTTP 502 空 body，造成 26 例假失败。本 fixture 在
整个测试会话期间剥离代理环境变量并把回环地址加入 no_proxy；不改生产代码。
"""

from __future__ import annotations

import os

import pytest

_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

_LOOPBACK_NO_PROXY = "127.0.0.1,localhost,::1"


@pytest.fixture(scope="session", autouse=True)
def _isolate_loopback_from_proxy() -> object:
    """会话级代理隔离：删代理变量 + 回环直连（yield fixture 保证恢复）。"""
    saved_proxy = {k: os.environ.pop(k) for k in _PROXY_VARS if k in os.environ}
    saved_no_proxy = os.environ.get("no_proxy")
    saved_NO_PROXY = os.environ.get("NO_PROXY")
    existing = ",".join(v for v in (saved_no_proxy, saved_NO_PROXY) if v)
    merged = f"{existing},{_LOOPBACK_NO_PROXY}" if existing else _LOOPBACK_NO_PROXY
    os.environ["no_proxy"] = merged
    os.environ["NO_PROXY"] = merged
    try:
        yield
    finally:
        os.environ.pop("no_proxy", None)
        os.environ.pop("NO_PROXY", None)
        if saved_no_proxy is not None:
            os.environ["no_proxy"] = saved_no_proxy
        if saved_NO_PROXY is not None:
            os.environ["NO_PROXY"] = saved_NO_PROXY
        os.environ.update(saved_proxy)
