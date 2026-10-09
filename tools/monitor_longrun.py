"""长测监控：后台每隔 N 秒采一次引擎状态，写成 CSV + 人类可读的时间线。

不弹任何窗口、不碰鼠标键盘 —— 专门给"一小时长测"用的。
引擎跑在 `citty run`（无界面服务）模式，字幕窗（Tk overlay）**不启动**。

    # 监控到 65 分钟后自动收尾并打印总结
    python tools/monitor_longrun.py --minutes 65 --interval 30

    # 随时手动看一眼当前状态（不写文件）
    python tools/monitor_longrun.py --once

采的字段：
    elapsed_min      已跑分钟
    segments         已切出的语音段数
    translated       已完成翻译的段数
    dropped/errors   丢弃/报错数
    srt_kb           SRT 文件大小（字幕在持续增长的证据）
    rss_mb           python 进程总内存（看有没有泄漏）
    cpu_pct          python 进程总 CPU
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
STATUS_URL = "http://127.0.0.1:8756/api/status"
SRT = OUT / "selftest.srt"


def snapshot(url: str) -> dict | None:
    try:
        r = httpx.get(url, timeout=6, trust_env=False)
        return r.json()
    except Exception:
        return None


_CPU_PREV: dict[int, tuple[float, float]] = {}
_LAST_TICK: list[float] = [0.0]


def procs() -> tuple[float, float]:
    """**只统计引擎自己**（命令行含 citty.cli）的 RSS(MB) 与 CPU(%) 总和。

    两个坑都在这里踩过：
    1) 不过滤进程名的话会把监控脚本自己、以及这台机器上别人的 python 都算进来，
       内存曲线就没意义了；
    2) `p.cpu_percent(interval=None)` 对**每个新建的 Process 对象**首次调用必定返回 0.0，
       而 process_iter 每轮都新建对象 —— 直接用会让整列 CPU 恒等于 0。
       所以这里改成按 `cpu_times()` 的增量算：ΔCPU秒 / Δ墙钟秒 / 核数 × 100。
    """
    try:
        import psutil
    except Exception:
        return 0.0, 0.0

    now = time.time()
    ncpu = psutil.cpu_count() or 1
    rss = 0.0
    busy = 0.0
    live: set[int] = set()
    for p in psutil.process_iter(["name", "cmdline", "memory_info"]):
        try:
            if "python" not in (p.info["name"] or "").lower():
                continue
            if "citty.cli" not in " ".join(p.info["cmdline"] or []):
                continue
            live.add(p.pid)
            mi = p.info["memory_info"]
            if mi:
                rss += mi.rss / 1048576
            ct = p.cpu_times()
            total = ct.user + ct.system
            prev = _CPU_PREV.get(p.pid)
            _CPU_PREV[p.pid] = (now, total)
            if prev:
                busy += max(total - prev[1], 0.0)
        except Exception:
            pass

    span = max(now - _LAST_TICK[0], 1e-6) if _LAST_TICK[0] else 0.0
    _LAST_TICK[0] = now
    cpu_pct = (busy / span / ncpu * 100.0) if span else 0.0
    return rss, cpu_pct


def row(st: dict | None, t0: float) -> dict:
    el = (time.time() - t0) / 60
    st = st or {}
    stats = st.get("stats") or {}
    rss, cpu = procs()
    return {
        "elapsed_min": round(el, 2),
        "segments": stats.get("segments", -1),
        "translated": stats.get("translated", -1),
        "dropped": stats.get("dropped", -1),
        "errors": stats.get("errors", -1),
        "clients": st.get("clients", -1),
        "srt_kb": round(SRT.stat().st_size / 1024, 1) if SRT.exists() else 0.0,
        "rss_mb": round(rss, 1),
        "cpu_pct": round(cpu, 1),
        "alive": bool(st),
        "at": datetime.now().strftime("%H:%M:%S"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="长测后台监控")
    ap.add_argument("--minutes", type=float, default=65.0, help="监控多久（分钟）")
    ap.add_argument("--interval", type=float, default=30.0, help="采样间隔（秒）")
    ap.add_argument("--url", default=STATUS_URL)
    ap.add_argument("--csv", default=str(OUT / "longrun.csv"))
    ap.add_argument("--once", action="store_true", help="只采一次并打印")
    args = ap.parse_args()

    if args.once:
        st = snapshot(args.url)
        r = row(st, time.time())
        print(f"引擎{'在线' if r['alive'] else '**离线**'}  {r}")
        return 0 if r["alive"] else 1

    t0 = time.time()
    deadline = t0 + args.minutes * 60
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["elapsed_min", "segments", "translated", "dropped", "errors",
              "clients", "srt_kb", "rss_mb", "cpu_pct", "alive", "at"]

    print(f"开始监控 {args.minutes:g} 分钟，每 {args.interval:g}s 一次 → {csv_path}")
    news = []
    dead_streak = 0
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        while time.time() < deadline:
            st = snapshot(args.url)
            r = row(st, t0)
            w.writerow(r)
            f.flush()
            line = (f"[{r['at']}] {r['elapsed_min']:6.2f}min 段{r['segments']:>4} 译{r['translated']:>4} "
                    f"丢{r['dropped']} 错{r['errors']} SRT{r['srt_kb']:>7.1f}KB "
                    f"RSS{r['rss_mb']:>7.1f}MB CPU{r['cpu_pct']:>5.1f}% {'在线' if r['alive'] else '**离线**'}")
            print(line, flush=True)
            news.append(r)
            if not r["alive"]:
                dead_streak += 1
                if dead_streak >= 3:
                    print("引擎连续 3 次无响应，停止监控", flush=True)
                    break
            else:
                dead_streak = 0
            time.sleep(args.interval)

    if news:
        first, last = news[0], news[-1]
        span = max(last["elapsed_min"] - first["elapsed_min"], 1e-6)
        print("\n" + "=" * 68)
        print("长测总结")
        print("=" * 68)
        print(f"  运行         {last['elapsed_min']:.1f} 分钟（采样 {len(news)} 次）")
        print(f"  语音段       {last['segments']}（首次采样 {first['segments']}）")
        print(f"  完成翻译     {last['translated']}   丢弃 {last['dropped']}   报错 {last['errors']}")
        print(f"  翻译速率     {(last['translated'] - first['translated']) / span:.1f} 段/分钟")
        print(f"  SRT 体积     {first['srt_kb']:.1f} → {last['srt_kb']:.1f} KB")
        print(f"  内存         {first['rss_mb']:.0f} → {last['rss_mb']:.0f} MB "
              f"(增长 {last['rss_mb'] - first['rss_mb']:+.0f} MB)")
        print(f"  CPU 峰值     {max(x['cpu_pct'] for x in news):.0f}%   均值 "
              f"{sum(x['cpu_pct'] for x in news) / len(news):.0f}%")
        offline = sum(1 for x in news if not x["alive"])
        print(f"  引擎离线采样 {offline} / {len(news)}")
        print(f"  CSV          {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
