r"""开长测之前先用它验素材：时长、音量分布、静音段、抽几处转写看内容对不对。

    python tools/check_audio.py out\samples\某视频.wav
    python tools/check_audio.py out\samples\某视频.wav --probe-minutes 5,30,55

为什么必须做这一步：一次长测要占一小时，如果素材本身是**静音/纯音乐/音量过低**，
跑完只会得到"0 段"的结果，白等一小时还以为是引擎的问题。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from citty.audio import load_wav  # noqa: E402
from citty.segmenter import rms_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="长测素材体检")
    ap.add_argument("wav")
    ap.add_argument("--probe-minutes", default="5,30,55",
                    help="在这些时间点抽 8 秒做一次转写，确认是语音")
    ap.add_argument("--asr-model", default=str(ROOT / "models" / "sensevoice-onnx" / "model.int8.onnx"))
    ap.add_argument("--asr-tokens", default=str(ROOT / "models" / "sensevoice-onnx" / "tokens.txt"))
    args = ap.parse_args()

    path = Path(args.wav)
    if not path.exists():
        print(f"❌ 找不到 {path}")
        return 2

    pcm, sr = load_wav(str(path))
    dur = len(pcm) / sr
    print(f"文件: {path.name}")
    print(f"时长: {dur / 60:.2f} 分钟 ({dur:.0f}s)  采样率 {sr}  样本 {len(pcm):,}")

    speech_db = rms_db(pcm)
    peak = float(np.max(np.abs(pcm)))
    print(f"整体: {speech_db:.1f} dBFS  峰值 {peak:.3f}")

    # 每秒一格的音量分布，看有没有大片静音
    win = sr
    n = len(pcm) // win
    levels = np.array([rms_db(pcm[i * win:(i + 1) * win]) for i in range(n)])
    silent = int((levels < -55).sum())
    print(f"每秒音量: 最低 {levels.min():.1f} / 中位 {np.median(levels):.1f} / 最高 {levels.max():.1f} dBFS")
    print(f"静音秒数(< -55 dBFS): {silent} / {n} ({silent / max(n,1) * 100:.1f}%)")
    for i in range(0, n, max(1, n // 12)):
        bar = "#" * max(0, int((levels[i] + 60) / 2))
        print(f"  {i/60:5.0f}分 {levels[i]:7.1f} dBFS {bar}")

    if silent / max(n, 1) > 0.5:
        print("\n⚠️  一半以上时间是静音 —— 素材不适合做长测")
    elif speech_db < -45:
        print("\n⚠️  整体音量过低 —— 素材不适合做长测")

    # 抽查转写
    if args.asr_model and Path(args.asr_model).exists():
        from citty.asr import LocalSherpaASR

        be = LocalSherpaASR(args.asr_model, args.asr_tokens, num_threads=8)
        be.warmup()
        print("\n抽查转写（每个时间点取 8 秒）:")
        for m in [float(x) for x in args.probe_minutes.split(",") if x.strip()]:
            start = int(m * 60 * sr)
            seg = pcm[start:start + 8 * sr]
            if len(seg) < sr:
                continue
            r = be.transcribe(seg, sr)
            print(f"  {m:5.1f}分 ({r.latency_ms:5.0f}ms)  {r.text[:80]}")
    else:
        print("\n(本地 ASR 模型不在，跳过转写抽查)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
