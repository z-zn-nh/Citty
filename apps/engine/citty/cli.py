"""命令行入口。

  python -m citty.cli doctor                  # 自检：这台机器能不能跑、卡在哪
  python -m citty.cli selftest --seconds 30   # 无界面跑通全链路（mock 语音 → 真翻译）
  python -m citty.cli run                     # 启动引擎（含 WebSocket 与 overlay 页）
  python -m citty.cli overlay                 # 启动桌面置顶字幕窗
  python -m citty.cli file a.wav --seconds 60 # 用音频文件代替实时捕获
  python -m citty.cli translate "text" -t en  # 单独测翻译后端
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import Config, load_config


def apply_overrides(cfg: Config, overrides: list[str] | None) -> Config:
    for item in overrides or []:
        key, _, raw = item.partition("=")
        if not key:
            continue
        val: object = raw
        low = raw.strip().lower()
        if low in ("true", "false"):
            val = low == "true"
        elif low in ("null", "none", ""):
            val = None
        else:
            try:
                val = int(raw)
            except ValueError:
                try:
                    val = float(raw)
                except ValueError:
                    if raw.startswith(("{", "[")):
                        try:
                            val = json.loads(raw)
                        except json.JSONDecodeError:
                            val = raw
        cur: dict = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return cfg


def setup_logging(cfg: Config) -> None:
    level = getattr(logging, str(cfg.get_path("logging.level", "INFO")).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx 每个请求都打 INFO 日志，对实时管线太吵
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
async def _run_selftest(cfg: Config, seconds: float) -> int:
    from .hub import ConsoleHub
    from .session import Session

    hub = ConsoleHub()
    session = Session(cfg, hub)
    print("=" * 68)
    print(f"自检运行 {seconds:.0f}s | 语音源={cfg.get_path('audio.source')} "
          f"| ASR={session.asr.name} | 翻译={session.mt.name} → {session.tgt_lang}")
    print("=" * 68)
    await session.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await session.stop()

    srt = session.srt(bilingual=True)
    out_dir = Path(cfg["_out_dir"])
    srt_path = out_dir / "selftest.srt"
    if cfg.get_path("logging.save_srt", True) and srt:
        srt_path.write_text(srt, encoding="utf-8")

    print("-" * 68)
    print("统计:", json.dumps(session.stats, ensure_ascii=False))
    if srt:
        print(f"SRT 已写入: {srt_path}")
    if session.stats["translated"] == 0:
        print("❌ 没有任何一句完成翻译 —— 链路没跑通")
        return 1
    print(f"✅ 跑通：{session.stats['translated']} 句完成翻译")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="citty", description="Citty 实时视频翻译引擎")
    ap.add_argument("-c", "--config", default=None, help="配置文件路径")
    ap.add_argument("-o", "--override", action="append", default=[],
                    help="覆盖配置，如 -o mt.backend=mock -o audio.source=loopback")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_doc = sub.add_parser("doctor", help="环境自检")
    p_self = sub.add_parser("selftest", help="无界面跑通全链路")
    p_self.add_argument("--seconds", type=float, default=30.0)

    p_run = sub.add_parser("run", help="启动引擎服务")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)

    sub.add_parser("overlay", help="启动桌面字幕窗")

    p_file = sub.add_parser("file", help="用音频文件代替实时捕获")
    p_file.add_argument("path")
    p_file.add_argument("--seconds", type=float, default=60.0)
    p_file.add_argument("--no-loop", action="store_true")
    p_file.add_argument("--meta", default=None,
                        help="视频元数据 JSON（title/uploader/tags/partition），喂给领域路由器")

    p_tr = sub.add_parser("translate", help="单独测试翻译后端")
    p_tr.add_argument("text")
    p_tr.add_argument("-s", "--source", default="auto")
    p_tr.add_argument("-t", "--target", default="zh-Hans")

    p_rec = sub.add_parser("record", help="录一段系统声音，用于 ASR A/B")
    p_rec.add_argument("--seconds", type=float, default=30.0)
    p_rec.add_argument("--out", default=None)
    p_rec.add_argument("--source", default="loopback", choices=["loopback", "mic"])

    p_ab = sub.add_parser("ab", help="多个云端 ASR 并排对比（读 config.yaml 的 asr.candidates）")
    p_ab.add_argument("--wav", required=True)
    p_ab.add_argument("--ref", default=None, help="参考文本文件，给了就算 CER/WER")

    p_say = sub.add_parser("say", help="用免费 TTS 合成一段语音（P2 配音用，默认 edge 零 key 零部署）")
    p_say.add_argument("text", nargs="?", default=None)
    p_say.add_argument("-v", "--voice", default=None)
    p_say.add_argument("-r", "--rate", default=None, help="语速，如 +10%% / -10%%")
    p_say.add_argument("-o", "--out", default=None)
    p_say.add_argument("--list-voices", action="store_true", help="列出可用语音（可配合前缀过滤）")
    p_say.add_argument("--voice-prefix", default="")

    p_routes = sub.add_parser("routes", help="P3-a 路由埋点报表：领域分布 / 质量 / 埋点开销")
    p_routes.add_argument("--dir", default=None, help="路由日志目录（默认 out/routing）")
    p_routes.add_argument("--session", default=None, help="只看文件名含该串的会话")
    p_routes.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    args = ap.parse_args(argv)

    if args.cmd == "overlay":
        from .overlay_tk import main as overlay_main

        return overlay_main([])

    cfg = load_config(args.config)
    apply_overrides(cfg, args.override)
    setup_logging(cfg)

    if args.cmd == "doctor":
        from .doctor import run_doctor

        return run_doctor(cfg)

    if args.cmd == "translate":
        from .mt import make_mt

        mt = make_mt(cfg)
        res = mt.translate(args.text, args.source, args.target)
        print(f"[{res.model}] {res.latency_ms:.0f}ms")
        print(res.text)
        return 0

    if args.cmd == "record":
        from .bench import record_to_wav

        out = args.out or str(Path(cfg["_out_dir"]) / "sample.wav")
        record_to_wav(out, args.seconds, args.source,
                      int(cfg.get_path("audio.sample_rate", 16000)))
        return 0

    if args.cmd == "ab":
        from .bench import run_ab

        return run_ab(cfg, args.wav, args.ref)

    if args.cmd == "say":
        from .tts import make_tts

        if args.list_voices or args.text is None:
            from .tts import EdgeTTS

            vs = EdgeTTS.list_voices(args.voice_prefix)
            print(f"{len(vs)} 个语音：")
            for v in vs:
                print(f"  {v['ShortName']:<32} {v['Gender']:<7} {v.get('Locale','')}")
            return 0

        tts = make_tts(cfg)
        out = args.out or str(Path(cfg["_out_dir"]) / "tts.mp3")
        res = tts.say(args.text, out, voice=args.voice, rate=args.rate)
        print(f"[{tts.name} {res.voice}] {res.latency_ms:.0f}ms  → 音频 {res.duration_s:.2f}s "
              f"(RTF {res.rtf:.2f})  {len(res.audio) / 1024:.0f}KB")
        print(f"写入: {out}")
        for s in res.sentences:
            print(f"  {s['t_start']:6.2f}-{s['t_end']:6.2f}s  {s['text'][:40]}")
        return 0

    if args.cmd == "routes":
        from .routing_log import format_report, summarize

        log_dir = args.dir or str(Path(cfg["_out_dir"]) / "routing")
        summary = summarize(log_dir, session=args.session)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(format_report(summary))
        return 0

    if args.cmd == "selftest":
        return asyncio.run(_run_selftest(cfg, args.seconds))

    if args.cmd == "file":
        cfg["audio"]["source"] = "file"
        cfg["audio"]["file"] = str(Path(args.path).resolve())
        cfg["audio"]["loop_file"] = not args.no_loop
        if args.meta:
            try:
                # 用 utf-8-sig：Windows 上用记事本/PowerShell 存的 JSON 常带 BOM，
                # 按 utf-8 读会在第一个字符就炸（json 报 "Expecting value: line 1 column 1"）。
                cfg.setdefault("router", {})["meta"] = json.loads(
                    Path(args.meta).read_text(encoding="utf-8-sig"))
                print(f"元数据已注入: {cfg['router']['meta']}")
            except Exception as exc:
                print(f"⚠ --meta 读取失败（忽略，用配置里的 meta）: {exc}")
        return asyncio.run(_run_selftest(cfg, args.seconds))

    if args.cmd == "run":
        import uvicorn

        from .server import create_app

        host = args.host or cfg.get_path("server.host", "127.0.0.1")
        port = args.port or int(cfg.get_path("server.port", 8756))
        app = create_app(cfg)
        print(f"引擎启动: http://{host}:{port}/overlay   (字幕层，可做 OBS 浏览器源)")
        print(f"状态:     http://{host}:{port}/api/status")
        print(f"SRT:      http://{host}:{port}/api/srt")
        uvicorn.run(app, host=host, port=port, log_level="warning")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
