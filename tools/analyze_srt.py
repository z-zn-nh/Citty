r"""对一次运行产出的 SRT 做质量体检 —— 长测之后看这个。

    python tools/analyze_srt.py out\longrun.srt
    python tools/analyze_srt.py out\longrun.srt --show 12

看什么：
  · 覆盖时长、条数、每秒条数（切句是不是碎得离谱）
  · **标签残留**（SenseVoice 的 <|...|> 没清干净会留下 `per>` 这种碎片）
  · 空行/单字条（切句过碎的信号）
  · 重复条（ASR 在静音段幻听的典型症状）
  · 中英两行是否齐全
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path


def parse_srt(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.rstrip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        a = [int(x) for x in m.groups()]
        t0 = a[0] * 3600 + a[1] * 60 + a[2] + a[3] / 1000
        t1 = a[4] * 3600 + a[5] * 60 + a[6] + a[7] / 1000
        body = lines[2:]
        out.append({
            "idx": int(lines[0]),
            "t0": t0, "t1": t1, "dur": t1 - t0,
            "zh": body[0] if len(body) > 0 else "",
            "en": body[1] if len(body) > 1 else "",
            "raw": "\n".join(body),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SRT 质量体检")
    ap.add_argument("srt")
    ap.add_argument("--show", type=int, default=0, help="随机抽几条打印")
    ap.add_argument("--tag-re", default=r"[<>|]|\\u", help="标签残留的正则")
    args = ap.parse_args()

    p = Path(args.srt)
    if not p.exists():
        print(f"❌ 找不到 {p}")
        return 2
    segs = parse_srt(p.read_text(encoding="utf-8"))

    if not segs:
        print("❌ 没解析出字幕")
        return 1

    span = max(s["t1"] for s in segs) - min(s["t0"] for s in segs)
    durs = sorted(s["dur"] for s in segs)
    med = durs[len(durs) // 2]
    print(f"文件      {p.name}  {p.stat().st_size / 1024:.1f} KB")
    print(f"条数      {len(segs)}")
    print(f"覆盖      {span / 60:.1f} 分钟（首条 {min(s['t0'] for s in segs):.1f}s → 末条 {max(s['t1'] for s in segs):.1f}s）")
    print(f"覆盖率     字幕总时长 / 覆盖窗口 = {sum(durs) / span * 100:.0f}%")
    print(f"密度      {len(segs) / (span / 60):.1f} 条/分钟（{span / len(segs):.1f}s 一条）")
    print(f"单条时长   中位 {med:.1f}s  最短 {durs[0]:.1f}s  最长 {durs[-1]:.1f}s  最长 10% 分位 {durs[int(len(durs)*0.9)]:.1f}s")

    tag = re.compile(args.tag_re)
    tagged = [s for s in segs if tag.search(s["zh"]) or tag.search(s["en"])]
    print(f"\n标签残留    {len(tagged)} / {len(segs)} ({len(tagged)/len(segs)*100:.1f}%)")
    if tagged:
        for s in tagged[:6]:
            print(f"    #{s['idx']}  {s['en'][:70]}")

    short = [s for s in segs if len(s["zh"]) <= 2 or len(s["en"]) <= 3]
    print(f"\n极短条(≤2字) {len(short)} / {len(segs)} ({len(short)/len(segs)*100:.1f}%)")
    if short:
        for s in short[:8]:
            print(f"    #{s['idx']}  {s['dur']:.1f}s  {s['zh'][:20]} | {s['en'][:40]}")

    miss_zh = sum(1 for s in segs if not s["zh"].strip())
    miss_en = sum(1 for s in segs if not s["en"].strip())
    print(f"\n缺中文行 {miss_zh}   缺英文行 {miss_en}")

    dups = [t for t, c in Counter(s["en"].strip() for s in segs if len(s["en"]) > 6).items() if c >= 5]
    print(f"重复出现的英文句子(≥5 次): {len(dups)} 种")
    for d in dups[:5]:
        print(f"    ×{Counter(s['en'].strip() for s in segs)[d]:2}  {d[:60]}")

    cjk = sum(1 for s in segs if re.search(r"[\u4e00-\u9fff]", s["en"]))
    print(f"英文行里混进中文的: {cjk}（素材本身有中文时正常）")

    big = sorted(segs, key=lambda s: -s["dur"])[:5]
    print("\n最长的 5 条（切句是否该更早断开）:")
    for s in big:
        print(f"    #{s['idx']}  {s['dur']:.1f}s  {s['zh'][:34]}")

    if args.show:
        step = max(len(segs) // args.show, 1)
        print(f"\n抽样 {args.show} 条:")
        for s in segs[::step][:args.show]:
            print(f"    #{s['idx']}  {s['t0']/60:5.1f}分  {s['zh'][:36]}")
            print(f"              {s['en'][:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
