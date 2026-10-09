"""核心数据结构。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Stability = Literal["partial", "stable", "final"]


@dataclass
class SpeechSegment:
    """VAD 切出来的一段语音（音频域事实）。"""

    index: int
    t_start: float
    t_end: float
    pcm: Any  # np.ndarray float32, 16k mono


@dataclass
class Segment:
    """字幕对象：前端按 id 就地更新，rev 递增。"""

    id: str
    rev: int = 1
    t_start: float = 0.0
    t_end: float = 0.0
    src: str = ""
    tgt: str = ""
    src_lang: str = "auto"
    tgt_lang: str = "zh-Hans"
    stability: Stability = "partial"
    speaker: str | None = None
    domain: str | None = None
    model: str | None = None
    latency_ms: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[Segment], bilingual: bool = True) -> str:
    """导出 SRT：双语时译文在上、原文在下。"""
    lines: list[str] = []
    n = 0
    for seg in sorted(segments, key=lambda s: s.t_start):
        if not seg.tgt.strip() and not seg.src.strip():
            continue
        n += 1
        lines.append(str(n))
        lines.append(f"{srt_timestamp(seg.t_start)} --> {srt_timestamp(seg.t_end)}")
        if bilingual:
            if seg.tgt.strip():
                lines.append(seg.tgt.strip())
            if seg.src.strip():
                lines.append(seg.src.strip())
        else:
            lines.append((seg.tgt or seg.src).strip())
        lines.append("")
    return "\n".join(lines)
