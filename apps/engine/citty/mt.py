"""MT 后端：bilibili_free（免费无需 key）| openai_compat | mock。"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Sequence

from .config import Config, env
from .net import make_client
from .types import now_ms

log = logging.getLogger("citty.mt")

LANG_NAMES = {
    "zh": "简体中文", "zh-Hans": "简体中文", "zh-Hant": "繁体中文", "zh-CN": "简体中文",
    "en": "英语", "ja": "日语", "ko": "韩语", "es": "西班牙语", "fr": "法语", "de": "德语",
    "ru": "俄语", "pt": "葡萄牙语", "it": "意大利语", "ar": "阿拉伯语", "th": "泰语",
    "vi": "越南语", "id": "印尼语", "tr": "土耳其语", "hi": "印地语",
}

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

# 哔哩哔哩免费 API 会对非浏览器 UA 返回 412，必须带浏览器 UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


@dataclass
class MTResult:
    text: str
    latency_ms: float = 0.0
    model: str = ""


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def build_prompt(text: str, src: str, tgt: str, context: Sequence[str] = (),
                 glossary: dict | None = None) -> str:
    """硬约束（术语表）+ 上下文（前几句）+ 明确的输出格式要求。"""
    tgt_name = lang_name(tgt)
    parts: list[str] = []
    if context:
        joined = "\n".join(f"- {c}" for c in context if c.strip())
        if joined:
            parts.append(f"【前文】（仅供理解语境，不要翻译前文）\n{joined}\n")
    if glossary:
        pairs = "，".join(f"{k} → {v}" for k, v in glossary.items())
        parts.append(f"【术语对照·必须严格遵守】{pairs}\n")
    parts.append(f"【源文】\n{text}\n")
    parts.append(
        f"请将【源文】翻译成{tgt_name}，"
        f"直接输出译文，不要任何解释、不要引号、不要重复源文。"
    )
    return "\n".join(parts)


class MTBackend:
    name = "base"

    def translate(self, text: str, src: str, tgt: str,
                  context: Sequence[str] = (), glossary: dict | None = None) -> MTResult:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
class MockMT(MTBackend):
    name = "mock"

    def translate(self, text: str, src: str, tgt: str,
                  context: Sequence[str] = (), glossary: dict | None = None) -> MTResult:
        t0 = now_ms()
        time.sleep(0.08)
        out = f"［模拟译文·{lang_name(tgt)}］{text}"
        if glossary:
            for k, v in glossary.items():
                out = out.replace(k, v)
        return MTResult(text=out, latency_ms=now_ms() - t0, model="mock-mt")


# --------------------------------------------------------------------------- #
class _ChatMT(MTBackend):
    """OpenAI 兼容 /chat/completions 的通用实现。"""

    def __init__(self, base_url: str, model: str, api_key: str | None,
                 timeout_s: float = 30.0, temperature: float = 0.0,
                 max_tokens: int = 512, user_agent: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.user_agent = user_agent or BROWSER_UA
        self._client = make_client(timeout_s)

    def translate(self, text: str, src: str, tgt: str,
                  context: Sequence[str] = (), glossary: dict | None = None) -> MTResult:
        t0 = now_ms()
        prompt = build_prompt(text, src, tgt, context, glossary)
        headers = {"Content-Type": "application/json", "User-Agent": self.user_agent}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        r = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        payload = r.json()
        raw = payload["choices"][0]["message"]["content"] or ""
        clean = _FENCE.sub("", raw.strip()).strip()
        return MTResult(text=clean, latency_ms=now_ms() - t0, model=self.model)


class BilibiliFreeMT(_ChatMT):
    """哔哩哔哩 Index-Translate 免费公网 API（OpenAI 兼容，无需 key）。"""

    name = "bilibili_free"


class OpenAICompatMT(_ChatMT):
    name = "openai_compat"


def make_mt(cfg: Config) -> MTBackend:
    backend = str(cfg.get_path("mt.backend", "bilibili_free")).lower()
    if backend == "mock":
        return MockMT()
    if backend == "bilibili_free":
        bc = cfg.get_path("mt.bilibili_free", {}) or {}
        return BilibiliFreeMT(
            base_url=bc.get("base_url", "https://index-translate.bilibili.com/v1"),
            model=bc.get("model", "Index-Translate-35B-A3B"),
            api_key=None,
            timeout_s=float(bc.get("timeout_s", 30)),
        )
    if backend == "openai_compat":
        oc = cfg.get_path("mt.openai_compat", {}) or {}
        key = env(oc.get("api_key_env"))
        if not key:
            log.warning("未找到环境变量 %s —— 翻译请求可能 401。", oc.get("api_key_env"))
        return OpenAICompatMT(
            base_url=oc.get("base_url", "https://api.deepseek.com/v1"),
            model=oc.get("model", "deepseek-chat"),
            api_key=key,
            timeout_s=float(oc.get("timeout_s", 30)),
        )
    raise SystemExit(f"未知 mt.backend: {backend}")
