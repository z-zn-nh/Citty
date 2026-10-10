"""量一下：给"路由评测集"打标签到底要花多少时间 —— 全流程实测，不估算。

它回答三个问题：
  1. B 站**元数据**（标题/UP主/分区 tname）抓一条多久？→ 标签成本
  2. B 站**字幕**能不能不下载音频就拿到？覆盖率多少？→ 语料成本
  3. 拿不到字幕的那些，退化成"下载音频 + 本地 ASR"要多久？→ 兜底成本

用法：
    python tools\\probe_bili_meta.py                              # 默认 3 个关键词 × 2 条
    python tools\\probe_bili_meta.py --keywords "英语听力,数码评测,美食探店" --per 3
    python tools\\probe_bili_meta.py --json                       # 机器可读

实测结论写进 docs/17。这个脚本本身就是"评测集采集器"的雏形。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
           "Accept": "application/json, text/plain, */*"}

DEFAULT_KEYWORDS = "英语听力练习,数码评测,美食探店"


def make_client(cookie: str | None):
    """直连 B 站（走代理会断 SSL）。trust_env=False 顺便绕开本机 NO_PROXY 里的 [::1] 坑。

    先 GET 一次首页把 buvid3/b_nut 之类的匿名 cookie 收进 jar —— 字幕接口没有它们
    多半返回空表（实测见 docs/17）。有登录 cookie 时一并带上。
    """
    import httpx

    h = dict(HEADERS)
    if cookie:
        h["Cookie"] = cookie
    client = httpx.Client(trust_env=False, timeout=15.0, headers=h, follow_redirects=True)
    try:
        client.get("https://www.bilibili.com/")
    except Exception:
        pass
    return client


def fetch_popular(client, n: int) -> list[dict]:
    """走公开的 popular 排行取样：**一次请求拿 N 条**，含分区 tname、cid、时长。

    比搜索接口稳：搜索在没登录/被风控时直接 412（实测）。
    """
    rows: list[dict] = []
    pn = 1
    while len(rows) < n and pn <= 5:
        data, dt, err = get_json(client, "https://api.bilibili.com/x/web-interface/popular",
                                 {"ps": min(20, n * 2), "pn": pn})
        if err or not data or data.get("code") != 0:
            print(f"  popular pn={pn} 失败: {err or (data or {}).get('message')}")
            break
        for e in ((data.get("data") or {}).get("list") or []):
            rows.append({
                "bvid": e.get("bvid") or "", "title": e.get("title") or "",
                "uploader": (e.get("owner") or {}).get("name") or "",
                "duration": float(e.get("duration") or 0),
                "cid": e.get("cid") or 0, "tname": e.get("tname") or "",
                "tname_v2": (e.get("tname_v2") or ""),
                "keyword": f"popular#{pn}",
            })
        pn += 1
    return rows[:n]


def search(keyword: str, per: int) -> tuple[list[dict], float]:
    """用 yt-dlp 搜。⚠ 实测搜索接口常返 412 风控，适合"能等"的场景，不适合批量采样。"""
    import yt_dlp

    t0 = time.time()
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "proxy": "",
                           "ignoreerrors": True, "nocheckcertificate": True}) as ydl:
        info = ydl.extract_info(f"bilisearch{per}:{keyword}", download=False)
    rows = []
    for e in (info.get("entries") or []):
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        bvid = ""
        for part in url.split("/"):
            if part.startswith("BV"):
                bvid = part.split("?")[0]
        if bvid:
            rows.append({"bvid": bvid, "title": e.get("title") or "",
                         "uploader": e.get("uploader") or "",
                         "duration": float(e.get("duration") or 0)})
    return rows, time.time() - t0


def get_json(client, url: str, params: dict | None = None) -> tuple[dict | None, float, str]:
    t0 = time.time()
    try:
        r = client.get(url, params=params)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, dt, f"HTTP {r.status_code}"
        return r.json(), dt, ""
    except Exception as exc:
        return None, time.time() - t0, f"{type(exc).__name__}: {exc}"


def probe_video(client, row: dict, want_subtitle_body: bool = True) -> dict:
    """一条视频走完：view 元数据 → player 字幕表 → 字幕正文。"""
    out = dict(row)
    out.setdefault("cid", 0)
    out.setdefault("tname", "")
    out.setdefault("tname_v2", "")
    out.update({"views": 0, "meta_s": 0.0, "sub_s": 0.0, "body_s": 0.0,
                "subs": [], "sub_lan": "", "sub_cues": 0, "sub_chars": 0,
                "err": "", "err_sub": "", "err_body": ""})

    if not out.get("cid"):
        data, dt, err = get_json(client, "https://api.bilibili.com/x/web-interface/view",
                                 {"bvid": row["bvid"]})
        out["meta_s"] = round(dt, 3)
        if err or not data or data.get("code") != 0:
            out["err"] = err or f"code={(data or {}).get('code')} {(data or {}).get('message', '')}"
            return out
        d = data["data"]
        out["tname"] = d.get("tname") or ""
        out["tname_v2"] = (d.get("tname_v2") or "").replace(">", " / ").strip(" /")
        out["cid"] = d.get("cid") or 0
        out["views"] = (d.get("stat") or {}).get("view") or 0

    # 字幕表：wbi/v2 与 v2 两个端点都试（不要 cookie 的那个才好批量采）
    ep_err: list[str] = []
    for ep in ("https://api.bilibili.com/x/player/wbi/v2",
               "https://api.bilibili.com/x/player/v2"):
        data, dt, err = get_json(client, ep, {"bvid": row["bvid"], "cid": out["cid"]})
        out["sub_s"] = round(out["sub_s"] + dt, 3)
        subs = (((data or {}).get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if subs:
            out["subs"] = [{"lan": s.get("lan"), "doc": s.get("lan_doc"),
                            "url": s.get("subtitle_url")} for s in subs]
            break
        ep_err.append(f"{ep.rsplit('/', 2)[-2]}/{ep.rsplit('/', 1)[-1]}: "
                      f"{err or ('code=' + str((data or {}).get('code')) + ' ' + str((data or {}).get('message', '')))}")
    out["err_sub"] = " | ".join(ep_err)

    if not out["subs"]:
        return out

    pick = out["subs"][0]
    url = pick["url"] or ""
    if url.startswith("//"):
        url = "https:" + url
    out["sub_lan"] = f"{pick['lan']}/{pick['doc']}"
    if not want_subtitle_body or not url:
        return out
    data, dt, err = get_json(client, url)
    out["body_s"] = round(dt, 3)
    if err or not data:
        out["err_body"] = err or "empty"
        return out
    body = data.get("body") or []
    out["sub_cues"] = len(body)
    out["sub_chars"] = sum(len(c.get("content") or "") for c in body)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="测 B 站元数据/字幕抓取成本（评测集可行性）")
    ap.add_argument("--source", choices=["popular", "search"], default="popular",
                    help="popular=公开排行一次请求拿 N 条（推荐）；search=搜索（常被 412 风控）")
    ap.add_argument("--n", type=int, default=8, help="popular 模式取几条")
    ap.add_argument("--keywords", default=DEFAULT_KEYWORDS, help="search 模式的关键词")
    ap.add_argument("--per", type=int, default=2, help="search 模式每个关键词取几条")
    ap.add_argument("--cookie", default=None, help="可选 SESSDATA=...（不登录时字幕常拿不到）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--asr-rtf", type=float, default=0.021,
                    help="兜底路线用：本地 SenseVoice 实测 RTF（docs/13）")
    args = ap.parse_args()

    client = make_client(args.cookie)
    try:
        seed = [c for c in client.cookies.jar] if args.cookie else [
            c for c in client.cookies.jar if c.name in ("buvid3", "b_nut", "buvid4")]
        print(f"  首页 cookie 预热度: {len(client.cookies.jar)} 个"
              f"（{'带登录态' if args.cookie else '匿名: ' + ', '.join(c.name for c in seed)}）")
    except Exception:
        pass

    rows: list[dict] = []
    search_s = 0.0
    if args.source == "popular":
        t0 = time.time()
        rows = fetch_popular(client, args.n)
        search_s = time.time() - t0
        print(f"  popular 取样 {search_s:.2f}s → {len(rows)} 条")
    else:
        for kw in [k.strip() for k in args.keywords.split(",") if k.strip()]:
            got, dt = search(kw, args.per)
            search_s += dt
            print(f"  搜索「{kw}」{dt:.1f}s → {len(got)} 条")
            for g in got:
                g["keyword"] = kw
            rows += got

    if not rows:
        print("没取到任何视频 —— 取样链路本身有问题")
        return 1

    print(f"\n逐条抓取（{len(rows)} 条，cookie={'有' if args.cookie else '无'}）")
    results = []
    for r in rows:
        out = probe_video(client, r)
        results.append(out)
        tag = "✅" if out["sub_cues"] else ("⚠" if out["subs"] else "❌")
        dur = f"{out['duration'] / 60:.0f}分" if out["duration"] else "?"
        print(f"  {tag} {out['bvid']}  {dur:>5}  分区={out['tname_v2'] or out['tname'] or '?':12} "
              f"meta={out['meta_s']:.2f}s sub={out['sub_s']:.2f}s body={out['body_s']:.2f}s "
              f"字幕={out['sub_cues']}条/{out['sub_chars']}字")
        if out["err"]:
            print(f"      元数据失败: {out['err']}")
        elif not out["subs"] and out["err_sub"]:
            print(f"      字幕表为空: {out['err_sub']}")

    n = len(results)
    with_meta = [r for r in results if not r["err"]]
    with_sub = [r for r in results if r["sub_cues"]]
    t_api = [r["meta_s"] + r["sub_s"] + r["body_s"] for r in with_meta]
    per_api = statistics.median(t_api) if t_api else 0.0
    total_min = sum(r["duration"] for r in results) / 60

    rtf = max(0.01, args.asr_rtf)
    audio_min_per_video = 4.0     # 实测：1 小时音频≈下载 1~3 分钟 + ffmpeg 转码 0.5~1 分钟
    asr_min_per_video = 60 * rtf  # 1 小时音频的 ASR 墙钟（8 线程 CPU）

    summary = {
        "videos": n, "meta_ok": len(with_meta), "subtitle_ok": len(with_sub),
        "subtitle_coverage": round(len(with_sub) / n, 3) if n else 0.0,
        "median_api_s_per_video": round(per_api, 3),
        "search_total_s": round(search_s, 1),
        "keywords": [k.strip() for k in args.keywords.split(",") if k.strip()],
        "cookie": bool(args.cookie),
        "sample_total_minutes": round(total_min, 1),
        "per_video": results,
    }

    print("\n" + "=" * 78)
    print(f"样本 {n} 条（合计 {total_min:.0f} 分钟视频）｜元数据成功 {len(with_meta)}｜"
          f"拿到字幕 {len(with_sub)}（覆盖 {summary['subtitle_coverage'] * 100:.0f}%）")
    print(f"单条 API 成本中位数 {per_api:.2f}s（元数据+字幕表+字幕正文）"
          f"，加搜索摊销 ≈ {per_api + search_s / n:.1f}s/条")
    print("=" * 78)
    print(f"\n【路线 A】只要标签（分区）不碰音频：100 条 ≈ {(per_api + search_s / n) * 100 / 60:.1f} 分钟")
    if with_sub:
        print(f"【路线 B】标签 + 字幕语料（零音频零 ASR）：100 条 ≈ "
              f"{(per_api + search_s / n) * 100 / 60:.1f} 分钟  ← 前提是字幕覆盖 {summary['subtitle_coverage'] * 100:.0f}%")
    miss = 1 - summary["subtitle_coverage"]
    print(f"【路线 C】拿不到字幕的那 {miss * 100:.0f}% 退化走音频+ASR：每条 1 小时视频 ≈ "
          f"{audio_min_per_video + asr_min_per_video:.0f} 分钟"
          f"（下载转码 ~{audio_min_per_video:.0f} + ASR {asr_min_per_video:.0f}）")
    print(f"        → 100 条里若有 {miss * 100:.0f} 条没字幕，额外 "
          f"{100 * miss * (audio_min_per_video + asr_min_per_video) / 60:.1f} 小时")

    if args.json:
        print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
