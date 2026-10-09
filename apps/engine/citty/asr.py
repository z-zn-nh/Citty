"""ASR 后端：mock | openai_compat（硅基流动 / Groq / OpenAI / 阿里百炼 等）。"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import time
import wave
from dataclasses import dataclass

import numpy as np

from .config import env, Config
from .net import make_client
from .types import now_ms

log = logging.getLogger("citty.asr")


@dataclass
class ASRResult:
    text: str
    lang: str = "auto"
    latency_ms: float = 0.0
    model: str = ""


def pcm_to_wav_bytes(pcm: np.ndarray, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return buf.getvalue()


class ASRBackend:
    name = "base"

    def transcribe(self, pcm: np.ndarray, sr: int, t_start: float = 0.0) -> ASRResult:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# mock：把合成语音"识别"成固定英文句子（科技/影视/游戏等，方便看翻译效果）
# --------------------------------------------------------------------------- #
MOCK_SENTENCES = [
    "So the new chip runs at 3.2 gigahertz and it is remarkably power efficient.",
    "The neural network was trained on a large corpus of subtitles from old movies.",
    "Latency matters more than raw accuracy when you are watching live video.",
    "Carbon fiber is expensive, but it resists cracking much better than steel.",
    "Let's take a look at the benchmark results before we ship this thing.",
    "Honestly, nobody expected the router model to be this fast in production.",
    "We should keep the original audio and just duck it under the translation.",
    "That is exactly the kind of sentence where a glossary saves your day.",
]


class MockASR(ASRBackend):
    name = "mock"

    def __init__(self, delay_ms: int = 260):
        self.delay_ms = delay_ms
        self._n = 0

    def transcribe(self, pcm: np.ndarray, sr: int, t_start: float = 0.0) -> ASRResult:
        t0 = now_ms()
        time.sleep(self.delay_ms / 1000.0)
        text = MOCK_SENTENCES[self._n % len(MOCK_SENTENCES)]
        self._n += 1
        return ASRResult(text=text, lang="en", latency_ms=now_ms() - t0, model="mock-asr")


# --------------------------------------------------------------------------- #
# OpenAI 兼容转录接口：POST {base}/audio/transcriptions  (multipart)
# --------------------------------------------------------------------------- #
class OpenAICompatASR(ASRBackend):
    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None,
                 language: str | None = None, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.language = language
        self.timeout_s = timeout_s
        self._client = make_client(timeout_s)

    def transcribe(self, pcm: np.ndarray, sr: int, t_start: float = 0.0) -> ASRResult:
        t0 = now_ms()
        wav = pcm_to_wav_bytes(pcm, sr)
        files = {"file": ("chunk.wav", wav, "audio/wav")}
        data = {"model": self.model, "response_format": "json"}
        if self.language:
            data["language"] = self.language
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = self._client.post(f"{self.base_url}/audio/transcriptions",
                              files=files, data=data, headers=headers)
        r.raise_for_status()
        payload = r.json()
        text = (payload.get("text") or "").strip()
        return ASRResult(text=text, lang=self.language or "auto",
                         latency_ms=now_ms() - t0, model=self.model)


class ChatAudioASR(ASRBackend):
    """走 /chat/completions 的音频输入（OpenAI 多模态 input_audio 格式）。

    适用：阿里百炼 qwen3-asr-flash、自建 vLLM 的 Qwen3-ASR 等
    ——这类模型把 ASR 当成"带音频的对话"，不是 /audio/transcriptions。
    """

    name = "chat_audio"

    def __init__(self, base_url: str, model: str, api_key: str | None,
                 language: str | None = None, prompt: str = "Transcribe the audio.",
                 timeout_s: float = 60.0, data_style: str = "auto",
                 extra_body: dict | None = None, itn: bool | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.language = language
        self.prompt = prompt
        self.timeout_s = timeout_s
        self.data_style = self._resolve_style(data_style, self.base_url)
        self.extra_body = dict(extra_body or {})
        self.itn = itn
        self._client = make_client(timeout_s)

    @staticmethod
    def _resolve_style(style: str, base_url: str) -> str:
        """auto: DashScope/百炼 要 data URI，其余（vLLM/OpenAI 兼容）要裸 base64。

        两家都收 `input_audio.data`，但格式不同：
          - OpenAI/vLLM:  {"data": "<base64>", "format": "wav"}
          - 阿里百炼:      {"data": "data:audio/wav;base64,<base64>"}
        """
        s = str(style or "auto").lower()
        if s in ("raw", "base64", "openai"):
            return "raw"
        if s in ("data_uri", "datauri", "dashscope"):
            return "data_uri"
        return "data_uri" if ("dashscope" in base_url or "aliyuncs" in base_url) else "raw"

    def _audio_field(self, b64: str) -> dict:
        if self.data_style == "data_uri":
            return {"data": f"data:audio/wav;base64,{b64}"}
        return {"data": b64, "format": "wav"}

    def build_body(self, pcm: np.ndarray, sr: int) -> dict:
        """构造请求体（抽出来便于离线自测，不发网络请求）。"""
        raw = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        b64 = base64.b64encode(raw).decode("ascii")
        prompt = self.prompt
        if self.language:
            prompt = f"{prompt} Language: {self.language}."
        body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "input_audio", "input_audio": self._audio_field(b64)},
                ],
            }],
            "temperature": 0,
        }
        extra = dict(self.extra_body)
        # 百炼的 asr_options 支持 ITN（把"三点二吉赫兹"规范成"3.2GHz"）—— 字幕场景很值
        if self.itn is not None or self.language:
            opts = dict(extra.get("asr_options") or {})
            if self.itn is not None:
                opts["enable_itn"] = bool(self.itn)
            if self.language:
                opts["language"] = self.language
            extra["asr_options"] = opts
        body.update(extra)
        return body

    def transcribe(self, pcm: np.ndarray, sr: int, t_start: float = 0.0) -> ASRResult:
        t0 = now_ms()
        body = self.build_body(pcm, sr)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        payload = r.json()
        text = (payload["choices"][0]["message"]["content"] or "").strip()
        # 有些实现会回 "language English<asr_text>...</asr_text>" 这类包装
        m = re.search(r"<asr_text>(.*?)</asr_text>", text, re.S)
        if m:
            text = m.group(1).strip()
        return ASRResult(text=text, lang=self.language or "auto",
                         latency_ms=now_ms() - t0, model=self.model)


_TAG_FULL = re.compile(r"<\|[^|]*\|>")      # 完整标签 <|zh|> <|NEUTRAL|> <|withitn|>
_TAG_JUNK = re.compile(r"[<>|]")            # 半截标签残渣里剩下的裸符号
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%])")
_DUP_PUNCT = re.compile(r"([,;:!?])\1+")    # 逗号重复（`week,,`），故意不含 `.` 以免压坏省略号
_MULTISPACE = re.compile(r"\s{2,}")


def clean_sense_voice_text(text: str) -> str:
    """清掉 SenseVoice 的标签与半截标签残渣。

    输入 → 输出（都是长测里真实出现过的）::

        "They said we'd hear back by this weekma So I'm trying to be patient>."
        → "They said we'd hear back by this weekma So I'm trying to be patient."

        "I keep refreshing my email like a crazy personperio>."
        → "I keep refreshing my email like a crazy personperio."

        "we'd hear back by this week , So I'm trying to be patient"
        → "we'd hear back by this week, So I'm trying to be patient"

    注意**做不到**的事：`weekma` 里的 `ma`、`personperio` 里的 `perio` 是模型拼错的
    词内碎片，靠字符串规则没法安全去掉（"drama" 去掉 `ma` 就废了），只能靠修采集链路
    （`audio.SoundcardSource` 的丢块处理）从源头减少。别为了这几个字加激进规则。
    """
    t = _TAG_FULL.sub("", text)
    t = _TAG_JUNK.sub("", t)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    t = _DUP_PUNCT.sub(r"\1", t)
    return _MULTISPACE.sub(" ", t).strip()


class LocalSherpaASR(ASRBackend):
    """本地 CPU 推理：sherpa-onnx + SenseVoice int8 ONNX。

    这是"完全本地、零 key、零显存"的那条路：
      · 权重只有 **0.22 GB**（model.int8.onnx）+ tokens.txt
      · 依赖只有 **onnxruntime**（sherpa-onnx 装完 28 MB），**不需要 torch、不需要 CUDA**
      · provider 可设 cpu / cuda；CPU 上 16 核实测能实时（见 docs/13）

    模型下载（两个文件，约 0.22GB）：
      https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx
      https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt
    """

    name = "local_sherpa"

    def __init__(self, model: str, tokens: str, num_threads: int = 4,
                 language: str | None = None, use_itn: bool = True,
                 provider: str = "cpu", sample_rate: int = 16000):
        self.model_path = str(model)
        self.tokens_path = str(tokens)
        self.num_threads = int(num_threads)
        self.language = language or ""
        self.use_itn = bool(use_itn)
        self.provider = provider
        self.sample_rate = int(sample_rate)
        self._rec = None

    def _ensure(self):
        if self._rec is not None:
            return self._rec
        import os

        import sherpa_onnx

        for p in (self.model_path, self.tokens_path):
            if not os.path.exists(p):
                raise SystemExit(
                    f"本地 ASR 模型文件缺失：{p}\n"
                    "见 docs/13-P1本地部署最简方案.md 的下载命令，或把 asr.backend 改回 openai_compat/mock。")
        t0 = now_ms()
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.model_path,
            tokens=self.tokens_path,
            num_threads=self.num_threads,
            sample_rate=self.sample_rate,
            language=self.language,
            use_itn=self.use_itn,
            provider=self.provider,
            debug=False,
        )
        log.info("本地 SenseVoice 载入完成 %.0fms (%s, threads=%d, itn=%s)",
                 now_ms() - t0, self.provider, self.num_threads, self.use_itn)
        return self._rec

    def warmup(self) -> None:
        """提前把模型读进内存，避免第一句字幕慢一拍。"""
        self._ensure()

    def transcribe(self, pcm: np.ndarray, sr: int, t_start: float = 0.0) -> ASRResult:
        rec = self._ensure()
        x = np.asarray(pcm, dtype=np.float32)
        if sr != self.sample_rate:
            from .audio import resample

            x = resample(x, sr, self.sample_rate)
        t0 = now_ms()
        stream = rec.create_stream()
        stream.accept_waveform(self.sample_rate, np.clip(x, -1.0, 1.0))
        rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
        # SenseVoice 会带 <|zh|><|NEUTRAL|><|Speech|><|itn|> 这类标签，要清掉。
        #
        # 62 分钟长测实测：994 句里 90 句（9.1%）尾巴带着碎片 —— `patient>`、`weekma`、
        # `personperio>`、`outio>`。词表里 `<`(9702) `>`(9704) `|`(9714) 是**独立 token**，
        # 模型在段落边界（尤其是 loopback 采集丢了块、音频被硬拼接的位置）松动时，
        # 就会把标签拆成碎片吐出来，只匹配完整的 `<|x|>` 不够。
        # 统计下来 90 条里 85% 是"单词后面直接跟一个 `>`"，所以清 `[<>|]` 能干掉绝大部分。
        # 口语字幕里这三个符号没有正当用途（"小于 5" 模型会写成"小于五"）。
        if os.environ.get("CITTY_DEBUG_RAW_ASR"):
            log.warning("ASR 原始输出: %r", stream.result.text)
        text = clean_sense_voice_text(text)
        return ASRResult(text=text, lang=self.language or "auto",
                         latency_ms=now_ms() - t0, model="sensevoice-onnx-int8")


def make_asr_from_profile(prof: dict) -> ASRBackend:
    """按候选配置构造后端（供 A/B 与配置化切换用）。"""
    style = str(prof.get("api_style", "transcriptions")).lower()
    common = dict(
        base_url=prof.get("base_url", ""),
        model=prof.get("model", ""),
        api_key=env(prof.get("api_key_env")),
        language=prof.get("language") or None,
        timeout_s=float(prof.get("timeout_s", 60)),
    )
    if style in ("chat", "chat_audio", "chat_completions"):
        common.update(
            data_style=prof.get("data_style", "auto"),
            extra_body=prof.get("extra_body") or {},
            itn=prof.get("itn"),
            prompt=prof.get("prompt", "Transcribe the audio."),
        )
        return ChatAudioASR(**common)
    return OpenAICompatASR(**common)


def make_asr(cfg: Config) -> ASRBackend:
    backend = str(cfg.get_path("asr.backend", "mock")).lower()
    if backend == "mock":
        return MockASR()
    if backend in ("local", "local_sherpa", "sherpa", "sensevoice_local"):
        lc = cfg.get_path("asr.local", {}) or {}
        be = LocalSherpaASR(
            model=lc.get("model", ""),
            tokens=lc.get("tokens", ""),
            num_threads=int(lc.get("num_threads", 4)),
            language=lc.get("language"),
            use_itn=bool(lc.get("use_itn", True)),
            provider=lc.get("provider", "cpu"),
        )
        if lc.get("warmup", True):
            be.warmup()
        return be
    if backend in ("openai_compat", "chat_audio"):
        oc = cfg.get_path("asr.openai_compat", {}) or {}
        key_env = oc.get("api_key_env")
        if not env(key_env):
            log.warning("未找到环境变量 %s —— ASR 请求会以无 key 方式发送（大概率 401）。"
                        "请在 .env 里配置，或把 asr.backend 改回 mock。", key_env)
        prof = dict(oc)
        prof["api_style"] = oc.get("api_style") or backend
        return make_asr_from_profile(prof)
    raise SystemExit(f"未知 asr.backend: {backend}")
