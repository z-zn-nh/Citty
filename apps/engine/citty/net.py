"""HTTP 客户端构造：把"环境变量把 httpx 弄崩"这一整类问题挡在入口处。

真实踩到的坑（Windows 上很常见）：
    NO_PROXY = "localhost,127.0.0.1,::1,...,.ts.net,.local,[::1]"
    HTTP_PROXY = "http://127.0.0.1:6544"
此时 `httpx.Client(...)` **在构造阶段**就抛：
    httpx.InvalidURL: Invalid port: ':1]'
原因：httpx 解析 NO_PROXY 时会为每个条目建 URLPattern，`[::1]` 被它拼成 `all://*[::1]` 之后解析失败。
后果：请求还没发出去，整个引擎就挂了——现象看着像"网络不通"，其实是本地配置问题。

处理：先按环境变量正常建；失败就退化成 `trust_env=False`（忽略代理设置）并打一条警告。
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("citty.net")


def make_client(timeout: float, **kwargs) -> httpx.Client:
    """按环境变量建 httpx.Client，环境变量有问题就退化成不用代理。"""
    try:
        return httpx.Client(timeout=timeout, **kwargs)
    except Exception as exc:  # httpx.InvalidURL 等
        log.warning("httpx 读取代理环境变量失败（%s: %s），改用 trust_env=False 直连",
                    type(exc).__name__, exc)
        kwargs.pop("trust_env", None)
        return httpx.Client(timeout=timeout, trust_env=False, **kwargs)


def proxy_report() -> dict:
    """给 doctor 用：报告当前代理环境，方便一眼看出问题。"""
    import os

    out: dict = {}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        v = os.environ.get(k)
        if v:
            out[k] = v
    try:
        httpx.Client(timeout=1)
        out["_client_ok"] = True
    except Exception as exc:
        out["_client_ok"] = False
        out["_client_error"] = f"{type(exc).__name__}: {exc}"
    return out
