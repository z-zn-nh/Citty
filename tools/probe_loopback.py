"""验证"抓系统声音"这一步在本机是否真的可用。

做法：一边用默认扬声器播放一段"像人说话"的信号，一边用 WASAPI loopback 录下来，
再用引擎自己的 VAD 判断能不能切出一句话。能切出来 = 抓系统声音可用。

    python tools/probe_loopback.py            # 只测默认设备
    python tools/probe_loopback.py --sweep    # 逐个设备配对试一遍（默认设备失败时用这个）

为什么要 --sweep：这台机器上只有**虚拟音频设备**（Senary Audio / 网易虚拟音频设备）时，
默认设备的 loopback 经常是空流（录到 -180 dBFS 的纯静音）。逐个配对试一遍才能
确定"是这台机器根本没有真实声卡"还是"只是默认设备选错了"。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "engine"))

from citty.segmenter import Segmenter, rms_db  # noqa: E402

SR = 16000


def speechlike(dur_s: float, level: float = 0.06) -> np.ndarray:
    n = int(SR * dur_s)
    t = np.arange(n) / SR
    sig = (
        np.sin(2 * np.pi * 120 * t)
        + 0.45 * np.sin(2 * np.pi * 240 * t)
        + 0.22 * np.sin(2 * np.pi * 360 * t)
    )
    sig *= 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * 4 * t))
    return (sig * level).astype(np.float32)


def _pair_test(sc, speaker, dur_play: float = 1.6, dur_rec: float = 2.2) -> dict:
    """对一对 (扬声器, 它的 loopback) 播放+录制，返回结果字典。"""
    out = {"speaker": str(speaker.name), "peak": 0.0, "db": -200.0, "err": ""}
    try:
        mic = sc.get_microphone(str(speaker.name), include_loopback=True)
    except Exception as exc:
        out["err"] = f"取 loopback 失败: {exc}"
        return out
    out["loopback"] = str(mic.name)

    sig = np.concatenate([
        np.zeros(int(SR * 0.15), dtype=np.float32),
        speechlike(dur_play),
        np.zeros(int(SR * 0.35), dtype=np.float32),
    ])

    def play():
        try:
            with speaker.player(samplerate=SR, channels=1, blocksize=1600) as p:
                p.play(sig)
        except Exception as exc:
            out["err"] = f"播放失败: {exc}"

    t = threading.Thread(target=play, daemon=True)
    t.start()
    time.sleep(0.1)
    try:
        block = int(SR * 0.05)
        with mic.recorder(samplerate=SR, channels=1, blocksize=block) as rec:
            rec.record(numframes=block)
            cap = rec.record(numframes=int(SR * dur_rec))
        mono = np.asarray(cap, dtype=np.float32).reshape(-1)
        out["peak"] = float(np.max(np.abs(mono))) if mono.size else 0.0
        out["db"] = rms_db(mono) if mono.size else -200.0
    except Exception as exc:
        out["err"] = f"录制失败: {exc}"
    t.join(timeout=3)
    return out


def sweep() -> int:
    import soundcard as sc

    speakers = list(sc.all_speakers())
    default = sc.default_speaker()
    print(f"发现 {len(speakers)} 个输出设备，默认 = {default.name}\n")
    good = []
    for sp in speakers:
        r = _pair_test(sc, sp)
        mark = "✅" if r["peak"] > 0.01 else "❌"
        print(f"{mark} {r['speaker']:<38} 峰值 {r['peak']:.4f}  {r['db']:7.1f} dBFS {r['err']}")
        if r["peak"] > 0.01:
            good.append(r["speaker"])
    print("-" * 66)
    if good:
        print(f"✅ 可用的输出设备：{good}")
        print("   把 config.yaml 的 audio.device 设成上面某一个（原名照抄）")
        return 0
    print("❌ 所有设备的 loopback 都是纯静音 —— 这台机器没有可用的声音通路。")
    print("   结论：实时捕获测不了（不是代码问题）。用 audio.source=file 测链路，")
    print("   真机实时测试请到有声卡的 Windows 上做。")
    return 1


def main() -> int:
    try:
        import soundcard as sc
    except Exception as exc:
        print(f"❌ soundcard 不可用: {exc}")
        return 2

    if "--sweep" in sys.argv:
        return sweep()

    speaker = sc.default_speaker()
    mic = sc.get_microphone(str(speaker.name), include_loopback=True)
    print(f"默认扬声器 : {speaker.name}")
    print(f"loopback 源: {mic.name}")

    signal = np.concatenate([
        np.zeros(int(SR * 0.5), dtype=np.float32),
        speechlike(2.0),
        np.zeros(int(SR * 0.8), dtype=np.float32),
        speechlike(1.5),
        np.zeros(int(SR * 0.5), dtype=np.float32),
    ])

    def play():
        try:
            with speaker.player(samplerate=SR, channels=1, blocksize=1600) as p:
                p.play(signal)
        except Exception as exc:  # pragma: no cover
            print(f"⚠️  播放失败: {exc}")

    threading.Thread(target=play, daemon=True).start()
    time.sleep(0.15)

    block = int(SR * 0.05)
    with mic.recorder(samplerate=SR, channels=1, blocksize=block) as rec:
        rec.record(numframes=block)  # 预热
        captured = rec.record(numframes=len(signal))

    mono = np.asarray(captured, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    print(f"录到 {len(mono)/SR:.1f}s，峰值 {peak:.4f}，整体 {rms_db(mono):.1f} dBFS")

    seg = Segmenter(sample_rate=SR)
    segments = []
    for i in range(0, len(mono) - block, block):
        segments.extend(seg.push(mono[i:i + block]))
    segments.extend(seg.flush())

    for s in segments:
        print(f"  切出语音段 #{s.index}: {s.t_start:.2f}s → {s.t_end:.2f}s "
              f"({(s.t_end - s.t_start):.2f}s, {rms_db(s.pcm):.1f} dBFS)")

    ok = peak > 0.01 and len(segments) >= 1
    print("-" * 60)
    if ok:
        print(f"✅ 系统声音捕获可用：切出 {len(segments)} 段（预期 2 段左右）")
        return 0
    print("❌ 没抓到场声：可能是默认输出设备不对、音量静音，或系统独占模式")
    print("   解决办法：确认正在播放声音；或把 config.yaml 的 audio.device 指定为具体设备名")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
