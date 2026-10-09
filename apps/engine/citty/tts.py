"""TTS 后端（为 P2 配音准备）。

最少部署原则下的默认选择：**edge** —— 用微软 Edge 的在线朗读服务，
**不需要 key、不需要下载任何模型权重、不占显存**，本机实测 RTF 0.15~0.26（中文 4 个语音档 + 300 多个总语音）。

代价（必须知道）：
  · 它是**非公开接口**，没有 SLA，可能被限流或变更；不支持音色克隆。
  · 音频会离开本机（送到微软的服务）。要完全离线/要音色克隆，就得自建 CosyVoice2 / IndexTTS-2.5（要 GPU）。

真实时长以服务端返回的 SentenceBoundary 事件为准（本机实测：服务端报的时长与 mp3 帧数吻合）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("citty.tts")

DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+0%"


@dataclass
class TTSResult:
    audio: bytes = b""
    duration_s: float = 0.0
    latency_ms: float = 0.0
    voice: str = ""
    text: str = ""
    path: str = ""
    sentences: list = field(default_factory=list)

    @property
    def rtf(self) -> float:
        return (self.latency_ms / 1000.0) / self.duration_s if self.duration_s else 0.0


class TTSBackend:
    name = "base"

    def synthesize(self, text: str, voice: str | None = None,
                   rate: str | None = None) -> TTSResult:
        raise NotImplementedError


class EdgeTTS(TTSBackend):
    """免费、零 key、零权重。需要 `pip install edge-tts`。"""

    name = "edge"

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE,
                 volume: str = "+0%", pitch: str = "+0Hz"):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch

    @staticmethod
    def list_voices(prefix: str = "") -> list[dict]:
        import edge_tts

        vs = asyncio.run(edge_tts.list_voices())
        if prefix:
            vs = [v for v in vs if v["ShortName"].startswith(prefix)]
        return vs

    def synthesize(self, text: str, voice: str | None = None,
                   rate: str | None = None) -> TTSResult:
        import edge_tts

        voice = voice or self.voice
        t0 = time.perf_counter()
        buf = bytearray()
        sentences: list = []
        end_ticks = 0

        async def run() -> None:
            nonlocal end_ticks
            comm = edge_tts.Communicate(text, voice, rate=rate or self.rate,
                                        volume=self.volume, pitch=self.pitch)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
                elif chunk["type"] == "SentenceBoundary":
                    off = chunk["offset"] / 1e7
                    dur = chunk["duration"] / 1e7
                    sentences.append({"t_start": round(off, 2), "t_end": round(off + dur, 2),
                                      "text": chunk.get("text", "")})
                    end_ticks = max(end_ticks, chunk["offset"] + chunk["duration"])

        asyncio.run(run())
        return TTSResult(audio=bytes(buf), duration_s=end_ticks / 1e7,
                         latency_ms=(time.perf_counter() - t0) * 1000.0,
                         voice=voice, text=text, sentences=sentences)

    def say(self, text: str, out_path: str, voice: str | None = None,
            rate: str | None = None) -> TTSResult:
        res = self.synthesize(text, voice, rate)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(res.audio)
        res.path = str(out_path)
        return res


class MockTTS(TTSBackend):
    """不联网的替身：写一段等长静音 wav，用来验证"配音流水线"本身。"""

    name = "mock"

    def __init__(self, sample_rate: int = 24000, chars_per_second: float = 4.3):
        self.sample_rate = sample_rate
        self.cps = chars_per_second

    def synthesize(self, text: str, voice: str | None = None,
                   rate: str | None = None) -> TTSResult:
        import numpy as np
        import wave

        t0 = time.perf_counter()
        dur = max(0.5, len(text) / self.cps)
        pcm = (np.sin(2 * np.pi * 220.0 * np.arange(int(self.sample_rate * dur))
                      / self.sample_rate) * 0.02 * 32767).astype("<i2")
        import io

        bio = io.BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())
        return TTSResult(audio=bio.getvalue(), duration_s=dur,
                         latency_ms=(time.perf_counter() - t0) * 1000.0,
                         voice="mock", text=text)

    def say(self, text: str, out_path: str, voice: str | None = None,
            rate: str | None = None) -> TTSResult:
        res = self.synthesize(text, voice, rate)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(res.audio)
        res.path = str(out_path)
        return res


def make_tts(cfg) -> TTSBackend:
    backend = str(cfg.get_path("tts.backend", "edge")).lower()
    if backend == "mock":
        return MockTTS()
    oc = cfg.get_path("tts.edge", {}) or {}
    return EdgeTTS(voice=oc.get("voice", DEFAULT_VOICE),
                   rate=oc.get("rate", DEFAULT_RATE))
