"""ASR A/B 基准：录一段真实音频 → 同一段音频喂给多个云端 ASR → 并排对比。

用途：回答"到底哪个 ASR 更适合我们"，用你自己的视频、你自己的语言分布来测，而不是看别人的榜。

    # 1) 抓 30 秒系统声音（播着你想测的那个视频）
    python -m citty.cli record --seconds 30 --out out/sample.wav

    # 2) 三家并排跑（需要 .env 里对应的 key）
    python -m citty.cli ab --wav out/sample.wav

    # 3) 如果你手打了标准答案，可以算 CER/WER
    python -m citty.cli ab --wav out/sample.wav --ref out/ref.txt
"""
from __future__ import annotations

import logging
import re
import time
import wave
from pathlib import Path

import numpy as np

from .asr import ASRBackend, make_asr_from_profile
from .audio import SoundcardSource, load_wav
from .config import Config, env
from .types import now_ms

log = logging.getLogger("citty.bench")


# --------------------------------------------------------------------------- #
def record_to_wav(out_path: str, seconds: float = 30.0, source: str = "loopback",
                  sample_rate: int = 16000, device: str | None = None) -> str:
    """从系统声/麦克风录一段 wav，用于 A/B。"""
    src = SoundcardSource(sample_rate=sample_rate, block_ms=50, device=device,
                          loopback=(source == "loopback"))
    frames: list[np.ndarray] = []
    total = 0
    need = int(sample_rate * seconds)
    print(f"开始录制 {seconds:.0f}s（源={source}）… 现在去播放你要测的视频")
    for block in src.chunks():
        frames.append(block)
        total += len(block)
        done = total / sample_rate
        if int(done) % 5 == 0 and int(done) != int((total - len(block)) / sample_rate):
            print(f"  {done:.0f}s / {seconds:.0f}s")
        if total >= need:
            break

    pcm = np.concatenate(frames)[:need]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"✅ 已保存 {path} ({len(pcm)/sample_rate:.1f}s)")
    return str(path)


# --------------------------------------------------------------------------- #
def read_wav(path: str, target_sr: int = 16000) -> tuple[np.ndarray, int]:
    """读 wav（支持 8/16/24/32-bit）→ (float32 单声道, target_sr)。"""
    return load_wav(path, target_sr)


# --------------------------------------------------------------------------- #
def _levenshtein(a: list, b: list) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_PUNCT = "，。！？、；：“”‘’（）《》【】〈〉·,.!?;:\"'()[]{}<>-—…"


def _norm(text: str) -> str:
    t = text.strip().lower().translate({ord(c): None for c in _PUNCT})
    t = re.sub(r"\s+", " ", t)
    return t


def error_rates(hyp: str, ref: str) -> dict:
    """中文按字、英文按词的 CER / WER。"""
    h, r = _norm(hyp), _norm(ref)
    is_cjk = len(_CJK.findall(r)) >= max(1, len(r) // 4)
    if is_cjk:
        hs, rs = list(h.replace(" ", "")), list(r.replace(" ", ""))
        name = "CER(字)"
    else:
        hs, rs = h.split(), r.split()
        name = "WER(词)"
    if not rs:
        return {"metric": name, "value": None, "ins": 0, "del": 0, "sub": 0}
    d = _levenshtein(hs, rs)
    return {"metric": name, "value": d / len(rs), "errors": d, "ref_len": len(rs)}


# --------------------------------------------------------------------------- #
def run_ab(cfg: Config, wav_path: str, ref_path: str | None = None) -> int:
    candidates = cfg.get_path("asr.candidates", []) or []
    if not candidates:
        print("config.yaml 里没有配置 asr.candidates")
        return 2
    if not Path(wav_path).exists():
        print(f"❌ 找不到音频文件: {wav_path}")
        print("   先录一段：python -m citty.cli record --seconds 30 --out out/sample.wav")
        return 2

    pcm, sr = read_wav(wav_path, int(cfg.get_path("audio.sample_rate", 16000)))
    dur = len(pcm) / sr
    # utf-8-sig: Windows 记事本/PowerShell 写出来的参考文本常带 BOM，
    # 按 utf-8 读会多出一个隐形字符，把 CER/WER 抬高
    ref = (Path(ref_path).read_text(encoding="utf-8-sig").strip()
           if ref_path and Path(ref_path).exists() else None)

    print("=" * 78)
    print(f"ASR A/B：{wav_path}  音频 {dur:.1f}s  参考文本 {'有' if ref else '无'}")
    print("=" * 78)

    rows = []
    for prof in candidates:
        name = prof.get("name", prof.get("model", "?"))
        key_env = prof.get("api_key_env")
        if key_env and not env(key_env):
            print(f"— {name}: 跳过（环境变量 {key_env} 未设置）")
            continue
        try:
            backend: ASRBackend = make_asr_from_profile(prof)
        except Exception as exc:
            print(f"— {name}: 构造失败 {exc}")
            continue

        try:
            t0 = now_ms()
            res = backend.transcribe(pcm, sr)
            wall = (now_ms() - t0)
        except Exception as exc:
            print(f"— {name}: ❌ 失败 {type(exc).__name__}: {str(exc)[:180]}")
            rows.append({"name": name, "ok": False, "text": "", "wall_ms": wall if 'wall' in dir() else 0})
            continue

        rtf = (wall / 1000.0) / dur if dur else 0
        rate = error_rates(res.text, ref) if ref else None
        rows.append({"name": name, "ok": True, "text": res.text,
                     "wall_ms": wall, "infer_ms": res.latency_ms, "rtf": rtf, "err": rate})
        print(f"\n■ {name}  ({prof.get('model')})")
        print(f"  端到端 {wall:.0f}ms | 模型自报 {res.latency_ms:.0f}ms | RTF {rtf:.3f} | 语种 {res.lang}")
        if rate:
            print(f"  {rate['metric']}: {rate['value']*100:.1f}%  ({rate.get('errors')}/{rate['ref_len']})")
        print(f"  转写: {res.text}")

    ok_rows = [r for r in rows if r["ok"]]
    print("\n" + "-" * 78)
    if not ok_rows:
        print("❌ 没有任何候选成功（检查 key / base_url / 是否有该模型）")
        return 1
    print("汇总（延迟越低越好，错误率越低越好）：")
    for r in sorted(ok_rows, key=lambda x: x["wall_ms"]):
        err = f"{r['err']['value']*100:5.1f}% {r['err']['metric']}" if r.get("err") and r["err"]["value"] is not None else "   —"
        print(f"  {r['name']:<22} {r['wall_ms']:8.0f}ms  RTF {r['rtf']:.3f}  {err}")
    print("\n提示：RTF > 1 表示比实时慢；实时字幕建议 RTF < 0.5（还要留出翻译的时间）")
    return 0
