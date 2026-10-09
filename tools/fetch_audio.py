"""把任意视频链接（或 B 站搜索关键词）抓成 16kHz 单声道 WAV —— 测试素材 / P2 配音输入都能用。

为什么要它：yt-dlp 的 CLI 参数在这台机器的 PowerShell 里会被吃掉（`--proxy ""` 会把后面
那个 `--print` 当成代理主机名），而且 m4a 要转 WAV 还得单独找 ffmpeg。这里用 Python API
调 yt-dlp，解码用 `imageio-ffmpeg` 自带的 ffmpeg 二进制 —— 不装系统软件、不改 PATH。

    # 搜一个 40 分钟以上的英文长视频，下前 1 个候选
    python tools/fetch_audio.py --search "english podcast" --min-minutes 40

    # 直接给链接
    python tools/fetch_audio.py --url "https://www.bilibili.com/video/BVxxxxxxxxx"

    # 只要清单不要下载
    python tools/fetch_audio.py --search "MIT 公开课" --min-minutes 50 --list-only

    # 限制时长（只要前 10 分钟，省空间）
    python tools/fetch_audio.py --url URL --max-minutes 10

输出：out\\samples\\<标题>.wav（16kHz 单声道，引擎直接能吃）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "out" / "samples"

# 这台机器的环境变量里有大小写重复的代理项，而且代理会让 B 站 SSL 直接断，
# 所以抓素材一律直连（cli 里显式传 proxy='' ）。
YDL_COMMON = {
    "quiet": True,
    "no_warnings": True,
    "proxy": "",
    "nocheckcertificate": True,
    "retries": 3,
}


def safe_name(title: str, limit: int = 60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", title).strip(" ._")
    return (s[:limit] or "video").strip(" ._")


def search(keyword: str, min_minutes: float, max_results: int = 10) -> list[dict]:
    """搜 B 站并解析出时长。

    注意**不能**用 extract_flat：扁平模式下 B 站搜索结果的 duration/title 全是空的
    （实测 duration=0.0、title=''），根本没法按时长挑。所以老老实实逐个解析元数据，
    因此 max_results 别开太大（每个都要一次请求）。
    """
    import yt_dlp

    print(f"搜索: {keyword}（解析前 {max_results} 个结果的元数据，约十几秒）")
    rows = []
    with yt_dlp.YoutubeDL({**YDL_COMMON, "ignoreerrors": True}) as ydl:
        info = ydl.extract_info(f"bilisearch{max_results}:{keyword}", download=False)
    for e in (info.get("entries") or []):
        if not e:
            continue
        dur = float(e.get("duration") or 0)
        if dur <= 0:
            continue
        rows.append({
            "title": e.get("title") or "",
            "duration": dur,
            "url": e.get("webpage_url") or e.get("url") or "",
            "uploader": e.get("uploader") or "",
        })
    rows.sort(key=lambda r: -r["duration"])
    kept = [r for r in rows if r["duration"] >= min_minutes * 60]
    print(f"  解析到 {len(rows)} 个，其中 ≥{min_minutes:g} 分钟的有 {len(kept)} 个")
    return kept or rows


def to_wav(src: Path, dst: Path, max_minutes: float | None) -> Path:
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if max_minutes:
        cmd += ["-t", str(int(max_minutes * 60))]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    print(f"  转 WAV: 16kHz 单声道{'（截前 %g 分钟）' % max_minutes if max_minutes else ''}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg 失败: {r.stderr[:400]}")
    return dst


def download_and_convert(url: str, out_dir: Path, max_minutes: float | None,
                         keep_source: bool = False) -> Path:
    import yt_dlp

    tmp = out_dir / "_dl"
    tmp.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with yt_dlp.YoutubeDL({
        **YDL_COMMON,
        "format": "bestaudio/best",
        "outtmpl": str(tmp / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "progress": False,
    }) as ydl:
        info = ydl.extract_info(url, download=True)
        src = Path(ydl.prepare_filename(info))

    size_mb = src.stat().st_size / 1048576
    print(f"  下载完成 {src.name}  {size_mb:.1f} MB  {time.time() - t0:.0f}s")

    dst = out_dir / f"{safe_name(info.get('title') or src.stem)}.wav"
    to_wav(src, dst, max_minutes)
    if not keep_source:
        src.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
    print(f"  ✅ {dst}  ({dst.stat().st_size / 1048576:.1f} MB)")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="抓视频音频 → 16k 单声道 WAV")
    ap.add_argument("--url", help="视频链接")
    ap.add_argument("--search", help="B 站搜索关键词（与 --url 二选一）")
    ap.add_argument("--min-minutes", type=float, default=30.0, help="搜索时过滤掉短于这个时长的")
    ap.add_argument("--max-minutes", type=float, default=None, help="只保留前 N 分钟（省磁盘）")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--list-only", action="store_true", help="只列候选，不下载")
    ap.add_argument("--keep-source", action="store_true", help="保留原始音频文件")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.search and not args.url:
        rows = search(args.search, args.min_minutes)
        if not rows:
            print(f"没搜到 ≥{args.min_minutes:g} 分钟的候选")
            return 1
        print(f"\n找到 {len(rows)} 个 ≥{args.min_minutes:g} 分钟的候选：")
        for i, r in enumerate(rows[:10]):
            print(f"  [{i}] {r['duration'] / 60:6.1f} 分钟  {r['title'][:56]}")
            print(f"       {r['url']}")
        if args.list_only:
            return 0
        args.url = rows[0]["url"]
        print(f"\n选第一个：{rows[0]['title'][:60]}")

    if not args.url:
        ap.print_help()
        return 2

    download_and_convert(args.url, out_dir, args.max_minutes, args.keep_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
