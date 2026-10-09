"""能量 VAD + 分句。纯 numpy，无额外依赖，跑在 CPU 上（把显存/内存留给云端模型）。"""
from __future__ import annotations

import logging

import numpy as np

from .types import SpeechSegment

log = logging.getLogger("citty.vad")


def rms_db(block: np.ndarray) -> float:
    if block.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return 20.0 * np.log10(max(rms, 1e-9))


class Segmenter:
    """状态机：idle → speaking →（静音达阈值 或 超长）→ 提交一句。"""

    def __init__(self, sample_rate: int = 16000, threshold_db: float = -42.0,
                 min_speech_ms: int = 250, silence_commit_ms: int = 350,
                 max_segment_ms: int = 8000, min_segment_ms: int = 400,
                 pad_ms: int = 120):
        self.sr = sample_rate
        self.threshold_db = threshold_db
        self.min_speech = int(sample_rate * min_speech_ms / 1000)
        self.silence_commit = int(sample_rate * silence_commit_ms / 1000)
        self.max_segment = int(sample_rate * max_segment_ms / 1000)
        self.min_segment = int(sample_rate * min_segment_ms / 1000)
        self.pad = int(sample_rate * pad_ms / 1000)

        self._buf: list[np.ndarray] = []
        self._speech_samples = 0
        self._silence_run = 0
        self._started = False
        self._seg_index = 0
        self._t_cursor = 0.0  # 已送入的音频时间（秒）
        self._seg_start_t = 0.0
        self._tail: list[np.ndarray] = []

    # ------------------------------------------------------------------ #
    def push(self, block: np.ndarray) -> list[SpeechSegment]:
        """送入一块音频，返回本次提交完成的语音段（可能为空）。"""
        sr = self.sr
        block_len = len(block)
        db = rms_db(block)
        is_speech = db > self.threshold_db
        committed: list[SpeechSegment] = []

        if not self._started:
            if is_speech:
                self._started = True
                self._seg_start_t = max(0.0, self._t_cursor - len(self._tail) / sr)
                self._buf = list(self._tail)
                self._tail = []
                self._buf.append(block)
                self._speech_samples = block_len
                self._silence_run = 0
            else:
                self._tail.append(block)
                if sum(len(b) for b in self._tail) > self.pad:
                    self._tail.pop(0)
        else:
            self._buf.append(block)
            if is_speech:
                self._speech_samples += block_len
                self._silence_run = 0
            else:
                self._silence_run += block_len

            total = sum(len(b) for b in self._buf)
            long_enough = self._speech_samples >= self.min_speech
            if long_enough and (self._silence_run >= self.silence_commit or total >= self.max_segment):
                seg = self._flush(full_len=total)
                if seg is not None:
                    committed.append(seg)
            elif self._silence_run > self.silence_commit * 6:  # 一直静音，丢掉
                self._reset()

        self._t_cursor += block_len / sr
        return committed

    # ------------------------------------------------------------------ #
    def flush(self) -> list[SpeechSegment]:
        """流结束时把未完成的句子吐出来。"""
        if self._started and self._buf:
            total = sum(len(b) for b in self._buf)
            seg = self._flush(full_len=total)
            return [seg] if seg else []
        return []

    def _flush(self, full_len: int) -> SpeechSegment | None:
        pcm = np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.float32)
        t_start = self._seg_start_t
        t_end = self._seg_start_t + len(pcm) / self.sr
        self._reset()
        if len(pcm) < self.min_segment:
            return None
        self._seg_index += 1
        return SpeechSegment(index=self._seg_index, t_start=t_start, t_end=t_end, pcm=pcm)

    def _reset(self) -> None:
        self._buf = []
        self._speech_samples = 0
        self._silence_run = 0
        self._started = False


def make_segmenter(cfg) -> Segmenter:
    v = cfg.get("vad", {}) or {}
    return Segmenter(
        sample_rate=int(cfg.get_path("audio.sample_rate", 16000)),
        threshold_db=float(v.get("energy_threshold_db", -42.0)),
        min_speech_ms=int(v.get("min_speech_ms", 250)),
        silence_commit_ms=int(v.get("silence_commit_ms", 350)),
        max_segment_ms=int(v.get("max_segment_ms", 8000)),
        min_segment_ms=int(v.get("min_segment_ms", 400)),
    )
