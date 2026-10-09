"""音频源：mock（合成语音）/ loopback（系统声）/ mic / file。

统一契约：`chunks()` 阻塞式产出 (np.float32 单声道 16k 数组)，每个数组 block_ms 长。
时间轴由**采样点计数**推导（音频时钟），不依赖墙钟，避免漂移。
"""
from __future__ import annotations

import logging
import math
import time
import warnings
import wave
from pathlib import Path
from typing import Iterator

import numpy as np

log = logging.getLogger("citty.audio")


# --------------------------------------------------------------------------- #
# WAV 解码（8/16/24/32-bit PCM → float32 单声道）
# --------------------------------------------------------------------------- #
def decode_pcm(raw: bytes, sampwidth: int, nchannels: int) -> np.ndarray:
    """把裸 PCM 字节解成 float32 单声道（-1.0 ~ 1.0）。

    24-bit 是很多录音笔/视频导出的默认位深，之前只处理 16-bit 会直接报
    "buffer size must be a multiple of element size"。
    """
    frame_bytes = max(1, sampwidth * max(1, nchannels))
    usable = len(raw) - (len(raw) % frame_bytes)
    raw = raw[:usable]
    if not raw:
        return np.zeros(0, dtype=np.float32)

    if sampwidth == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sampwidth == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = (v ^ 0x800000) - 0x800000          # 24-bit 符号扩展
        data = v.astype(np.float32) / 8388608.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的位深: {sampwidth * 8} bit")

    if nchannels > 1:
        data = data.reshape(-1, nchannels).mean(axis=1)
    return data.astype(np.float32)


def resample(data: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr or data.size == 0:
        return data
    n_out = int(len(data) * target_sr / sr)
    return np.interp(np.linspace(0, len(data) - 1, n_out),
                     np.arange(len(data)), data).astype(np.float32)


def load_wav(path: str | Path, target_sr: int = 16000) -> tuple[np.ndarray, int]:
    """读任意位深/声道/采样率的 wav → (float32 单声道, target_sr)。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    return resample(decode_pcm(raw, width, ch), sr, target_sr), target_sr


# --------------------------------------------------------------------------- #
# 设备探测
# --------------------------------------------------------------------------- #
def list_devices() -> dict:
    """列出扬声器/麦克风/loopback 设备，供 doctor 使用。"""
    out: dict = {"speakers": [], "microphones": [], "loopback": [], "error": None}
    try:
        import soundcard as sc
    except Exception as exc:  # pragma: no cover
        out["error"] = f"soundcard 不可用: {exc}"
        return out
    try:
        out["speakers"] = [str(s.name) for s in sc.all_speakers()]
        out["microphones"] = [str(m.name) for m in sc.all_microphones(include_loopback=False)]
        out["loopback"] = [str(m.name) for m in sc.all_microphones(include_loopback=True)]
    except Exception as exc:  # pragma: no cover
        out["error"] = f"枚举失败: {exc}"
    return out


def _loopback_mic(device: str | None):
    import soundcard as sc

    if device:
        return sc.get_microphone(device, include_loopback=True)
    speaker = sc.default_speaker()
    return sc.get_microphone(str(speaker.name), include_loopback=True)


# --------------------------------------------------------------------------- #
# 基类
# --------------------------------------------------------------------------- #
class AudioSource:
    sample_rate: int = 16000

    def chunks(self) -> Iterator[np.ndarray]:  # pragma: no cover - 接口
        raise NotImplementedError

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# loopback / mic：真实系统音频
# --------------------------------------------------------------------------- #
# WASAPI loopback 在共享模式下会偶尔丢块，soundcard 会抛
# `SoundcardRuntimeWarning: data discontinuity in recording`。这不是噪音警告：
# 62 分钟长测实测，采集音频里出现的**硬拼接**会让 SenseVoice 在接缝处吐出
# `<|withitn|>` 这类标签的碎片（句子尾巴多出 `>`、`ma`），占当时 9% 的句子。
# 警告本身没法取消（丢的样点找不回来），但**必须能计数** —— 否则"字幕偶尔出怪字"
# 这类问题永远查不到根上。这里把警告变成计数器，采集线程每 30 秒记一次日志。
_DISCONTINUITIES = 0
_ORIG_SHOWWARNING = warnings.showwarning


def _counting_showwarning(message, category, filename, lineno, file=None, line=None):
    global _DISCONTINUITIES
    if category.__name__ == "SoundcardRuntimeWarning":
        _DISCONTINUITIES += 1
    return _ORIG_SHOWWARNING(message, category, filename, lineno, file=file, line=line)


def install_capture_diagnostics() -> None:
    """把 soundcard 的丢块警告接到计数器上（幂等，进程内装一次）。"""
    if warnings.showwarning is not _counting_showwarning:
        warnings.showwarning = _counting_showwarning


def capture_discontinuities() -> int:
    """本次进程内采集到的丢块次数（0 = 采集链路干净）。"""
    return _DISCONTINUITIES


class SoundcardSource(AudioSource):
    def __init__(self, sample_rate: int = 16000, block_ms: int = 50,
                 device: str | None = None, loopback: bool = True):
        self.sample_rate = sample_rate
        self.block_frames = max(160, int(sample_rate * block_ms / 1000))
        self.device = device
        self.loopback = loopback
        self._rec = None
        self.short_reads = 0
        self.blocks = 0

    def chunks(self) -> Iterator[np.ndarray]:
        import soundcard as sc

        install_capture_diagnostics()
        mic = _loopback_mic(self.device) if self.loopback else (
            sc.get_microphone(self.device) if self.device else sc.default_microphone()
        )
        log.info("音频源: %s (loopback=%s, %d Hz, block=%d frames)",
                 mic, self.loopback, self.sample_rate, self.block_frames)
        with mic.recorder(samplerate=self.sample_rate, channels=1,
                          blocksize=self.block_frames) as rec:
            self._rec = rec
            while True:
                data = rec.record(numframes=self.block_frames)
                block = np.asarray(data, dtype=np.float32).reshape(-1)
                self.blocks += 1
                if block.size != self.block_frames:
                    # 短读必须补齐：下面是按"每块都是 block_frames 个样点"推进音频时钟的，
                    # 少给几个样点就等于时钟悄悄变快，字幕时间轴会一路漂。
                    self.short_reads += 1
                    block = np.pad(block, (0, max(0, self.block_frames - block.size)))[:self.block_frames]
                if self.blocks % 600 == 0:  # 约 30 秒一次
                    lost = capture_discontinuities()
                    if lost or self.short_reads:
                        log.warning("采集诊断: 运行 %d 块, 音频不连续 %d 次, 短读 %d 次"
                                    "（不连续=WASAPI 丢块，接缝处的音频已被硬拼）",
                                    self.blocks, lost, self.short_reads)
                yield block


# --------------------------------------------------------------------------- #
# file：读取 wav（便于离线验证）
# --------------------------------------------------------------------------- #
class FileSource(AudioSource):
    def __init__(self, path: str, sample_rate: int = 16000, block_ms: int = 50,
                 loop: bool = True, realtime: bool = True):
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.block_frames = max(160, int(sample_rate * block_ms / 1000))
        self.loop = loop
        # realtime=True: 按播放速度吐数据（否则音频时钟会瞬间跑到几万秒，
        # 一次 45 秒的测试能刷出几百个片段、拖垮 ASR 配额）
        self.realtime = realtime

    def _read(self) -> np.ndarray:
        data, _ = load_wav(self.path, self.sample_rate)
        return data

    def chunks(self) -> Iterator[np.ndarray]:
        data = self._read()
        log.info("音频源: file %s (%.1fs, realtime=%s)", self.path.name,
                 len(data) / self.sample_rate, self.realtime)
        pos = 0
        produced = 0
        t0 = time.perf_counter()
        while True:
            if pos + self.block_frames > len(data):
                if not self.loop:
                    tail = data[pos:]
                    if len(tail):
                        yield np.pad(tail, (0, self.block_frames - len(tail)))
                    return
                pos = 0
                continue
            block = data[pos:pos + self.block_frames]
            pos += self.block_frames
            produced += len(block)
            yield block
            if self.realtime:
                target = t0 + produced / self.sample_rate
                sleep = target - time.perf_counter()
                if sleep > 0:
                    time.sleep(sleep)


# --------------------------------------------------------------------------- #
# mock：合成"像人说话"的音频（谐波 + 音节包络），用来验证 VAD/切句/时间轴
# --------------------------------------------------------------------------- #
class MockAudioSource(AudioSource):
    """不需要麦克风、不需要 key，也能把 捕获→VAD→切句→(mock)ASR→MT→字幕 全链路跑起来。

    生成模式：说 2.5~4.5s、停 0.6s，循环。信号是 120Hz 基频的谐波叠加 + 4Hz 音节包络，
    足以让能量 VAD 正确判定"有语音/无语音"。
    """

    def __init__(self, sample_rate: int = 16000, block_ms: int = 50, speech_level: float = 0.22):
        self.sample_rate = sample_rate
        self.block_frames = max(160, int(sample_rate * block_ms / 1000))
        self.level = speech_level
        self._phase = 0.0
        self._t_speech = 0.0
        self._t_silence = 0.0

    def _burst_plan(self) -> tuple[float, float]:
        # 用固定序列，方便复现
        seq = [(3.2, 0.7), (4.4, 0.7), (2.6, 0.8), (3.8, 0.6), (3.0, 0.9)]
        idx = int((self._t_speech + self._t_silence) * 10) % len(seq)
        return seq[idx]

    def chunks(self) -> Iterator[np.ndarray]:
        log.info("音频源: mock（合成语音，无需麦克风/key）")
        t0 = time.perf_counter()
        produced = 0
        plan = list([(3.2, 0.7), (4.4, 0.7), (2.6, 0.8), (3.8, 0.6), (3.0, 0.9)])
        plan_idx = 0
        speech_left, silence_left = plan[0]
        while True:
            n = self.block_frames
            t = (np.arange(n) + produced) / self.sample_rate
            speaking = speech_left > 0
            if speaking:
                # 谐波（近似人声共振）+ 音节包络 + 少量噪声
                f0 = 118.0 + 8.0 * math.sin(2 * math.pi * 0.35 * t[0])
                sig = (
                    1.00 * np.sin(2 * math.pi * f0 * t)
                    + 0.45 * np.sin(2 * math.pi * 2 * f0 * t)
                    + 0.22 * np.sin(2 * math.pi * 3 * f0 * t)
                    + 0.10 * np.sin(2 * math.pi * 4.5 * f0 * t)
                )
                envelope = 0.55 + 0.45 * np.abs(np.sin(2 * math.pi * 4.0 * t))
                sig = sig * envelope
                noise = np.random.default_rng(0).normal(0, 0.012, n)
                block = (sig * self.level + noise).astype(np.float32)
            else:
                block = np.random.default_rng(1).normal(0, 0.0015, n).astype(np.float32)

            dt = n / self.sample_rate
            if speaking:
                speech_left -= dt
                if speech_left <= 0:
                    plan_idx = (plan_idx + 1) % len(plan)
                    _, silence_left = plan[plan_idx]
            else:
                silence_left -= dt
                if silence_left <= 0:
                    speech_left, _ = plan[plan_idx]
            produced += n
            yield block

            # 按真实时间产出（否则会瞬间跑完，测不出延迟）
            target = t0 + produced / self.sample_rate
            sleep = target - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)


def make_source(cfg) -> AudioSource:
    src = str(cfg.get_path("audio.source", "mock")).lower()
    sr = int(cfg.get_path("audio.sample_rate", 16000))
    block_ms = int(cfg.get_path("audio.block_ms", 50))
    device = cfg.get_path("audio.device") or None
    if src == "mock":
        return MockAudioSource(sr, block_ms)
    if src == "file":
        path = cfg.get_path("audio.file")
        if not path:
            raise SystemExit("audio.source=file 需要设置 audio.file")
        return FileSource(path, sr, block_ms, bool(cfg.get_path("audio.loop_file", True)),
                          realtime=bool(cfg.get_path("audio.file_realtime", True)))
    if src == "mic":
        return SoundcardSource(sr, block_ms, device, loopback=False)
    if src == "loopback":
        return SoundcardSource(sr, block_ms, device, loopback=True)
    raise SystemExit(f"未知 audio.source: {src}")
