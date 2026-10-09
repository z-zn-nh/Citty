"""环境自检：一条命令回答"这台机器现在能不能跑、卡在哪一步"。"""
from __future__ import annotations

import importlib
import socket
import shutil
import sys
import time
from pathlib import Path

from .config import Config, env

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def _line(mark: str, title: str, detail: str = "") -> None:
    print(f"{mark} {title}" + (f" — {detail}" if detail else ""))


def run_doctor(cfg: Config) -> int:
    problems = 0
    print("=" * 68)
    print("Citty 引擎自检")
    print("=" * 68)

    # 1) Python / Tk
    _line(OK, f"Python {sys.version.split()[0]}", sys.executable)
    try:
        importlib.import_module("tkinter")
        _line(OK, "tkinter 可用", "桌面字幕窗可以启动")
    except Exception as exc:
        problems += 1
        _line(BAD, "tkinter 不可用", f"{exc}（可用浏览器版 overlay 替代）")

    # 2) 依赖
    for mod in ("numpy", "httpx", "yaml", "fastapi", "uvicorn"):
        try:
            importlib.import_module(mod)
            _line(OK, f"依赖 {mod}")
        except Exception as exc:
            problems += 1
            _line(BAD, f"依赖 {mod} 缺失", str(exc))
    for mod in ("soundcard", "sounddevice", "websocket"):
        try:
            importlib.import_module(mod)
            _line(OK, f"依赖 {mod}")
        except Exception as exc:
            problems += 1
            _line(WARN, f"依赖 {mod} 缺失", f"{exc}（只影响捕获/字幕窗）")

    # 2.5) 代理环境（踩过一次：NO_PROXY 里的 [::1] 会让 httpx 在构造阶段就崩）
    from .net import proxy_report

    pr = proxy_report()
    if not pr.get("_client_ok", True):
        _line(WARN, "httpx 无法按环境变量建客户端", f"{pr.get('_client_error')} → 引擎会自动改用直连")
    elif pr.get("HTTP_PROXY") or pr.get("http_proxy"):
        _line(OK, "代理环境正常", f"HTTP_PROXY={pr.get('HTTP_PROXY') or pr.get('http_proxy')}")
    else:
        _line(OK, "无代理，直连")

    # 3) 音频设备
    from .audio import list_devices
    dev = list_devices()
    if dev.get("error"):
        problems += 1
        _line(WARN, "音频设备枚举失败", dev["error"])
    else:
        _line(OK, f"扬声器 {len(dev['speakers'])} 个", "; ".join(dev["speakers"][:2]))
        lb = dev["loopback"]
        _line(OK if lb else WARN, f"loopback 设备 {len(lb)} 个",
              "; ".join(lb[:2]) if lb else "若为 0，抓系统声音会失败")

    # 4) 翻译后端连通性（真发一条请求）
    backend = str(cfg.get_path("mt.backend", "bilibili_free"))
    if backend == "mock":
        _line(WARN, "翻译后端 = mock（离线占位）")
    else:
        try:
            from .mt import make_mt
            mt = make_mt(cfg)
            t0 = time.perf_counter()
            res = mt.translate("Latency matters more than raw accuracy.", "en", "zh-Hans")
            ms = (time.perf_counter() - t0) * 1000
            _line(OK, f"翻译后端 {backend} 连通", f"{ms:.0f}ms → {res.text[:40]}")
        except Exception as exc:
            problems += 1
            _line(BAD, f"翻译后端 {backend} 不通", str(exc)[:160])

    # 5) ASR 后端
    asr_backend = str(cfg.get_path("asr.backend", "mock"))
    if asr_backend == "mock":
        _line(WARN, "ASR 后端 = mock", "能跑通全链路，但转写是假的；拿到 key 后改 config.yaml")
    elif asr_backend in ("local", "local_sherpa", "sherpa", "sensevoice_local"):
        lc = cfg.get_path("asr.local", {}) or {}
        model_p, tok_p = lc.get("model", ""), lc.get("tokens", "")
        miss = [p for p in (model_p, tok_p) if not p or not Path(p).exists()]
        if miss:
            problems += 1
            _line(BAD, "本地 ASR 模型文件缺失", "; ".join(miss) + "（见 docs/13 的下载命令）")
        else:
            size_mb = Path(model_p).stat().st_size / 1048576
            _line(OK, f"本地 ASR = sherpa-onnx SenseVoice",
                  f"{size_mb:.0f}MB int8 / {lc.get('provider', 'cpu')} / threads={lc.get('num_threads', 4)}")
            try:
                import sherpa_onnx  # noqa: F401

                _line(OK, "依赖 sherpa-onnx 已装", "不需要 torch / CUDA")
            except Exception as exc:
                problems += 1
                _line(BAD, "依赖 sherpa-onnx 缺失", f"{exc} → pip install sherpa-onnx")
    else:
        key_env = cfg.get_path("asr.openai_compat.api_key_env")
        key = env(key_env)
        if key:
            _line(OK, f"ASR key 存在", f"{key_env} = {key[:6]}…")
        else:
            problems += 1
            _line(BAD, f"ASR key 缺失", f"请在 .env 里设置 {key_env}")

    # 6) ffmpeg（只有处理视频文件才需要）
    ff = shutil.which("ffmpeg")
    _line(OK if ff else WARN, "ffmpeg " + ("已安装" if ff else "未安装"),
          ff or "实时字幕不需要；只有给 mp4 配音/导出时才需要")

    # 7) 端口
    host = cfg.get_path("server.host", "127.0.0.1")
    port = int(cfg.get_path("server.port", 8756))
    with socket.socket() as s:
        free = s.connect_ex((host, port)) != 0
    _line(OK if free else WARN, f"端口 {host}:{port} " + ("空闲" if free else "已被占用"),
          "" if free else "引擎可能已在运行，或改 config.yaml 里的 port")

    print("-" * 68)
    print(f"结论：{'全部就绪' if problems == 0 else f'{problems} 项需要注意（不影响先跑 mock 全链路）'}")
    return 0
