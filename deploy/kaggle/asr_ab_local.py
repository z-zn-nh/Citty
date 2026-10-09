"""在**免费 GPU 环境**（Kaggle T4×2 / 飞桨 AI Studio V100）上做本地 ASR 选型 A/B。

为什么需要它：云端 A/B 只能测"人家提供的模型"，而"到底该选 SenseVoice / Whisper-turbo / Fun-ASR-Nano"
必须用**你自己视频的音频**在**本地模型**上测才作数。这个脚本把这件事变成"粘进 Notebook 跑一格"。

用法（Kaggle / AI Studio 的 Notebook 里）：
    !pip install -q funasr modelscope faster-whisper
    !python asr_ab_local.py --wav /kaggle/input/MY_WAVS/sample.wav --ref /kaggle/input/MY_WAVS/ref.txt
    # 没带参考文本也能跑（只出转写文本 + 延迟，不出 CER/WER）

本机自检（不吃 GPU、不装 funasr，只验证流程与指标计算）：
    python asr_ab_local.py --dry-run --wav out/samples/official_zh.wav --ref out/ref.txt

注意：**本脚本在本机只跑过 --dry-run**（本机没有可用 CUDA 的 torch、也没装 funasr），
真实模型那一段需要在 Kaggle/AI Studio 上首次运行时确认。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

# --------------------------------------------------------------------------- #
# 指标计算（与 citty/bench.py 完全同口径：中文按字算 CER、拉丁按词算 WER）
# --------------------------------------------------------------------------- #
_PUNCT = "，。！？、；：“”‘’（）《》【】〈〉·,.!?;:\"'()[]{}<>-—…"
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _norm(text: str) -> str:
    t = text.strip().lower().translate({ord(c): None for c in _PUNCT})
    return re.sub(r"\s+", " ", t)


def _lev(a, b) -> int:
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


def error_rates(hyp: str, ref: str) -> dict:
    """返回 {"metric", "value", "errors", "ref_len"}；中文按字、英文按词。"""
    h, r = _norm(hyp), _norm(ref)
    is_cjk = len(_CJK.findall(r)) >= max(1, len(r) // 4)
    if is_cjk:
        hs, rs, name = list(h.replace(" ", "")), list(r.replace(" ", "")), "CER(字)"
    else:
        hs, rs, name = h.split(), r.split(), "WER(词)"
    if not rs:
        return {"metric": name, "value": None, "errors": 0, "ref_len": 0}
    d = _lev(hs, rs)
    return {"metric": name, "value": round(d / len(rs), 4), "errors": d, "ref_len": len(rs)}


def wav_seconds(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# 候选后端（按需 import，缺依赖就跳过，不影响其它后端）
# --------------------------------------------------------------------------- #
class Backend:
    name = "base"
    device = "-"

    def load(self) -> None:
        raise NotImplementedError

    def transcribe(self, wav: str) -> str:
        raise NotImplementedError


class SenseVoiceBackend(Backend):
    name = "SenseVoiceSmall"

    def __init__(self, device: str = "auto"):
        self.want = device
        self.model = None

    def load(self) -> None:
        import torch
        from funasr import AutoModel

        self.device = "cuda:0" if (self.want == "auto" and torch.cuda.is_available()) else (
            self.want if self.want != "auto" else "cpu")
        self.model = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True,
                               device=self.device, disable_update=True)

    def transcribe(self, wav: str) -> str:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        res = self.model.generate(input=wav, language="auto", use_itn=True, batch_size_s=60)
        return rich_transcription_postprocess(res[0]["text"]) if res else ""


class FasterWhisperBackend(Backend):
    name = "whisper-large-v3-turbo"

    def __init__(self, device: str = "auto", size: str = "large-v3-turbo"):
        self.want = device
        self.size = size
        self.model = None

    def load(self) -> None:
        import torch
        from faster_whisper import WhisperModel

        self.device = "cuda" if (self.want == "auto" and torch.cuda.is_available()) else (
            self.want if self.want != "auto" else "cpu")
        # T4/V100 都不支持 bf16；fp16 在这两代卡上是稳的（P100 建议改 int8）
        compute = "float16" if self.device == "cuda" else "int8"
        self.model = WhisperModel(self.size, device=self.device, compute_type=compute)

    def transcribe(self, wav: str) -> str:
        segs, _info = self.model.transcribe(wav, beam_size=5, vad_filter=False)
        return "".join(s.text for s in segs).strip()


class FunASRNanoBackend(Backend):
    name = "Fun-ASR-Nano-2512"

    def __init__(self, device: str = "auto"):
        self.want = device
        self.model = None

    def load(self) -> None:
        import torch
        from funasr import AutoModel

        self.device = "cuda:0" if (self.want == "auto" and torch.cuda.is_available()) else "cpu"
        self.model = AutoModel(model="FunAudioLLM/Fun-ASR-Nano-2512", trust_remote_code=True,
                               device=self.device, disable_update=True)

    def transcribe(self, wav: str) -> str:
        res = self.model.generate(input=wav, cache={}, batch_size_s=60)
        return res[0]["text"] if res else ""


class DryRunBackend(Backend):
    """不打模型，返回一段可控文本，用来验证脚本流程与指标计算。"""

    name = "dry-run"

    def __init__(self, device: str = "-"):
        self.device = device

    def load(self) -> None:
        pass

    def transcribe(self, wav: str) -> str:
        ref = (os.environ.get("CITTY_DRYRUN_REF") or "").strip()
        if ref:
            # 故意改最后 1 个字，让指标非 0，验证指标真的在工作
            return ref[:-1] + ("错" if ref[-1] != "错" else "对")
        return "甚至出现交易几乎停滞的情况。"


# --------------------------------------------------------------------------- #
def build_backends(which: list[str], device: str, dry: bool) -> list[Backend]:
    out: list[Backend] = []
    for w in which:
        key = w.strip().lower()
        if dry:
            out.append(DryRunBackend())
            continue
        try:
            if key in ("sensevoice", "sv", "sensevoice-small"):
                out.append(SenseVoiceBackend(device))
            elif key in ("whisper", "fw", "turbo", "whisper-turbo"):
                out.append(FasterWhisperBackend(device))
            elif key in ("funasr", "nano", "fun-asr-nano"):
                out.append(FunASRNanoBackend(device))
            else:
                print(f"  !! 未知后端: {w}", file=sys.stderr)
        except Exception as exc:  # pragma: no cover
            print(f"  !! 初始化 {w} 失败: {exc}", file=sys.stderr)
    return out


def gpu_report() -> str:
    try:
        import torch

        if not torch.cuda.is_available():
            return "无 CUDA（跑在 CPU 上）"
        lines = []
        for i in range(torch.cuda.device_count()):
            p = torch.cuda.get_device_properties(i)
            lines.append(f"[{i}] {p.name} {p.total_memory / 1024**3:.1f}GB  sm_{p.major}{p.minor}")
        return " | ".join(lines)
    except Exception as exc:
        return f"torch 不可用: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True, help="待测音频（16k 单声道 wav 最稳）")
    ap.add_argument("--ref", default=None, help="参考文本 txt；给了就算 CER/WER")
    ap.add_argument("--backends", default="sensevoice,whisper,funasr")
    ap.add_argument("--device", default="auto", help="auto | cpu | cuda:0 | cuda:1")
    ap.add_argument("--repeat", type=int, default=3, help="每个后端跑几遍（第 1 遍算预热）")
    ap.add_argument("--dry-run", action="store_true", help="不打模型，只验证流程")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    wav = str(Path(args.wav).resolve())
    # utf-8-sig：Windows 记事本/PowerShell 写的参考文本常带 BOM，直接读会多出一个字符
    ref = (Path(args.ref).read_text(encoding="utf-8-sig").strip() if args.ref else "")
    dur = wav_seconds(wav)

    print("=" * 78)
    print("Citty · 免费 GPU 上的本地 ASR A/B")
    print(f"音频 : {wav}  ({dur:.2f}s)")
    print(f"参考 : {(ref[:60] + '…') if len(ref) > 60 else (ref or '（没给，只出文本和延迟）')}")
    print(f"GPU  : {gpu_report()}")
    print("=" * 78)

    rows = []
    for be in build_backends(args.backends.split(","), args.device, args.dry_run):
        print(f"\n--- {be.name} ---")
        t0 = time.perf_counter()
        try:
            be.load()
        except Exception as exc:
            print(f"  加载失败，跳过：{exc}")
            rows.append({"model": be.name, "error": str(exc)[:120]})
            continue
        load_s = time.perf_counter() - t0
        print(f"  加载 {load_s:.1f}s | device={be.device}")

        times, hyp = [], ""
        for i in range(max(1, args.repeat)):
            t1 = time.perf_counter()
            try:
                hyp = be.transcribe(wav)
            except Exception as exc:
                print(f"  第 {i + 1} 遍失败：{exc}")
                break
            times.append(time.perf_counter() - t1)
            print(f"  第 {i + 1} 遍 {times[-1] * 1000:7.0f} ms  RTF {times[-1] / dur:.3f}")
        if not times:
            rows.append({"model": be.name, "error": "transcribe failed"})
            continue

        warm = times[1:] or times           # 第 1 遍含预热
        best = min(warm)
        row = {"model": be.name, "device": be.device, "load_s": round(load_s, 2),
               "ms_best": round(best * 1000), "rtf_best": round(best / dur, 3),
               "ms_median": round(sorted(warm)[len(warm) // 2] * 1000), "text": hyp}
        if ref:
            row.update(error_rates(hyp, ref))
        rows.append(row)
        acc = ""
        if row.get("value") is not None:
            acc = f"  {row['metric']} {row['value']:.3f} ({row['errors']}/{row['ref_len']})"
        print(f"  → 最佳 {row['ms_best']} ms (RTF {row['rtf_best']}){acc}")
        print(f"  → {hyp[:100]}")

    print("\n" + "=" * 78)
    print(f"{'模型':<26}{'设备':<9}{'最佳ms':>8}{'RTF':>7}{'指标':>10}{'值':>8}")
    for r in rows:
        if r.get("error"):
            print(f"{r['model']:<26}{'跳过':<9}  {r['error'][:40]}")
            continue
        val = f"{r['value']:.3f}" if r.get("value") is not None else "-"
        print(f"{r['model']:<26}{r.get('device', '-'):<9}{r['ms_best']:>8}{r['rtf_best']:>7}"
              f"{r.get('metric', '-'):>10}{val:>8}")
    print("=" * 78)
    print("判据：CER/WER 差距 > 3 个百分点时优先质量；差距很小就选 RTF 最低的（实时字幕看延迟）。")

    if args.json_out:
        out = args.json_out
    elif Path("/kaggle").exists():
        out = "/kaggle/working/asr_ab.json"          # Kaggle：落在这能被 Save Version 保存
    else:
        out = str(Path(wav).parent.parent / "asr_ab.json")   # 本机：落在音频所在目录的上层
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"wav": wav, "seconds": dur, "gpu": gpu_report(),
                                     "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
