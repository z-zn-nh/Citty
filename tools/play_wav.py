"""把 WAV 播到**指定输出设备**——用来在没有视频的情况下喂给引擎，做实时链路测试。

为什么需要它：Windows 的 `System.Media.SoundPlayer` 只会播到**默认设备**。
如果默认设备是个"死流"（虚拟声卡的 loopback 抓不到东西），就会白白录到一片静音，
让人误以为"抓系统声音坏了"。这个脚本能把声音精确喂给某个设备。

    python tools/play_wav.py --list
    python tools/play_wav.py out\\samples\\official_en.wav
    python tools/play_wav.py out\\samples\\official_en.wav --device "扬声器 (网易虚拟音频设备)"
    python tools/play_wav.py out\\samples\\official_en.wav --device "扬声器 (网易虚拟音频设备)" --repeat 3

配合引擎实时链路测试（两个窗口）：
    窗口A：python -m citty.cli -c config.local.yaml -o audio.source=loopback ^
              -o "audio.device=扬声器 (网易虚拟音频设备)" selftest --seconds 30
    窗口B：python tools/play_wav.py out\\samples\\official_en.wav --device "扬声器 (网易虚拟音频设备)" --repeat 2
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "engine"))

from citty.audio import load_wav  # noqa: E402

SR = 16000


def main() -> int:
    ap = argparse.ArgumentParser(description="把 WAV 播到指定输出设备")
    ap.add_argument("wav", nargs="?", help="要播的 wav 文件")
    ap.add_argument("--device", default=None, help="输出设备名（不填=系统默认）")
    ap.add_argument("--repeat", type=int, default=1, help="重复播放次数")
    ap.add_argument("--gap", type=float, default=0.6, help="每遍之间的静音秒数")
    ap.add_argument("--start", type=float, default=0.0, help="从第几秒开始播（复现某段问题时用）")
    ap.add_argument("--seconds", type=float, default=0.0, help="只播多少秒（0=到结尾）")
    ap.add_argument("--list", action="store_true", help="列出所有输出设备")
    args = ap.parse_args()

    import soundcard as sc

    if args.list or not args.wav:
        default = sc.default_speaker()
        print(f"默认输出设备: {default.name}\n")
        for sp in sc.all_speakers():
            mark = " *" if sp.name == default.name else "  "
            print(f"{mark} {sp.name}")
        return 0

    path = Path(args.wav)
    if not path.exists():
        print(f"❌ 找不到 {path}")
        return 2

    pcm, sr = load_wav(str(path))
    if args.start or args.seconds:
        a = int(args.start * sr)
        b = int((args.start + args.seconds) * sr) if args.seconds else len(pcm)
        pcm = pcm[a:b]
        print(f"截取 {args.start:.1f}s → {args.start + len(pcm) / sr:.1f}s")
    speaker = sc.get_speaker(args.device) if args.device else sc.default_speaker()
    dur = len(pcm) / sr
    print(f"播放 {path.name}（{dur:.2f}s, {sr}Hz）→ {speaker.name}  ×{args.repeat}")
    for i in range(max(1, args.repeat)):
        if i:
            time.sleep(args.gap)
        with speaker.player(samplerate=sr, channels=1, blocksize=1600) as p:
            p.play(np.asarray(pcm, dtype=np.float32))
        print(f"  第 {i + 1}/{args.repeat} 遍完成")
    print("✅ 播放结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
