"""校验 ASR 的 key / 端点 / 调用形态是否真的能用（不打印密钥本体）。

    python tools/probe_asr_key.py            # 测 config.yaml 里 asr.openai_compat 那套
    python tools/probe_asr_key.py --list     # 依次测 asr.candidates 里的每一家

会做三件事：
  1) 看 key 有没有被读到（只显示长度与首尾各 4 位）
  2) GET /models 验证鉴权
  3) 用官方样例 wav 真跑一次转写，验证调用形态（chat_audio vs transcriptions）对不对
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "engine"))

from citty.asr import (ChatAudioASR, OpenAICompatASR, make_asr_from_profile,  # noqa: E402
                       pcm_to_wav_bytes)
from citty.config import env, load_config  # noqa: E402

# Qwen3-ASR 模型卡里给的官方样例（中文）
SAMPLE_ZH = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav"
SAMPLE_EN = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_en.wav"


def mask(v: str | None) -> str:
    if not v:
        return "(未设置)"
    if len(v) <= 10:
        return f"长度 {len(v)}（疑似不是 sk- 开头的 API Key）"
    return f"长度 {len(v)}, {v[:4]}…{v[-4:]}"


def check_auth(base_url: str, key: str | None) -> tuple[bool, str]:
    import httpx

    if not key:
        return False, "没有 key"
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except Exception as exc:
        return False, f"网络异常 {type(exc).__name__}: {exc}"
    if r.status_code == 200:
        try:
            ids = [m["id"] for m in r.json().get("data", [])]
            return True, f"HTTP 200，可见 {len(ids)} 个模型" + (f"（含 ASR: {[i for i in ids if 'asr' in i.lower()][:3]}）" if any("asr" in i.lower() for i in ids) else "")
        except Exception:
            return True, "HTTP 200"
    body = r.text[:160].replace("\n", " ")
    return False, f"HTTP {r.status_code}: {body}"


def real_transcribe(prof: dict) -> None:
    """用官方样例音频真跑一次（优先给 URL，服务端更省事）。"""
    import httpx

    key = env(prof.get("api_key_env"))
    base = prof.get("base_url", "").rstrip("/")
    model = prof.get("model", "")
    style = str(prof.get("api_style", "transcriptions")).lower()

    if style in ("chat", "chat_audio", "chat_completions"):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": SAMPLE_ZH}},
            ]}],
            "temperature": 0,
        }
        if prof.get("itn"):
            body["asr_options"] = {"enable_itn": True}
        r = httpx.post(f"{base}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}"}, timeout=90)
    else:
        wav = httpx.get(SAMPLE_ZH, timeout=60, follow_redirects=True).content
        r = httpx.post(f"{base}/audio/transcriptions",
                       files={"file": ("asr_zh.wav", wav, "audio/wav")},
                       data={"model": model, "response_format": "json"},
                       headers={"Authorization": f"Bearer {key}"}, timeout=90)

    print(f"   转写请求 HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"   ❌ {r.text[:300]}")
        return
    j = r.json()
    try:
        text = j["choices"][0]["message"]["content"]
    except Exception:
        text = j.get("text", "")
    print(f"   ✅ 识别结果: {str(text).strip()[:200]}")
    print("   （官方样例是中文语音，能出中文说明中文通路 OK）")


def test_profile(prof: dict, label: str) -> bool:
    name = prof.get("name") or prof.get("model") or label
    key_env = prof.get("api_key_env")
    key = env(key_env)
    print(f"\n■ {name}  ({prof.get('api_style', 'transcriptions')})")
    print(f"   base_url : {prof.get('base_url')}")
    print(f"   model    : {prof.get('model')}")
    print(f"   {key_env} : {mask(key)}")
    ok, msg = check_auth(prof.get("base_url", ""), key)
    print(f"   鉴权     : {'✅' if ok else '❌'} {msg}")
    if not ok:
        return False
    real_transcribe(prof)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="测 asr.candidates 全部")
    ap.add_argument("-c", "--config", default=r"D:\Citty\config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    print("=" * 74)
    print("ASR key / 端点 / 调用形态自检")
    print("=" * 74)

    profiles = cfg.get_path("asr.candidates", []) if args.list else [dict(
        cfg.get_path("asr.openai_compat", {}) or {}, name="asr.openai_compat")]

    results = [test_profile(p, f"#{i}") for i, p in enumerate(profiles)]

    print("\n" + "-" * 74)
    if any(results):
        print("✅ 至少一家可用。把可用的那家设成 config.yaml 的 asr.openai_compat，")
        print("   并把 asr.backend 从 mock 改成 openai_compat（或 chat_audio）。")
        return 0
    print("❌ 没有一家通过。常见原因：")
    print("   1. key 类型不对 —— 百炼要的是控制台里的「API-KEY」(sk- 开头)，")
    print("      不是阿里云账号的 AccessKey ID/Secret（那对是账号级的，权限过大且不用于模型 API）")
    print("   2. 该 key 没开通对应模型的权限 / 没实名 / 余额为 0")
    print("   3. base_url 与 key 不是同一家（百炼的 key 只能配 dashscope 的地址）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
