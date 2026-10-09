"""把 FunASR / SenseVoice 包成 **OpenAI 兼容**的 ASR 服务。

为什么要这个文件：本地引擎已经跑通了 `POST /v1/audio/transcriptions`（multipart 上传一段 wav）
这条代码路径（用本地假服务实测过 8 段语音 7 句翻译）。把自建 SenseVoice 也包成同一个接口，
**本地一行代码都不用改**，只是把 `base_url` 从硅基流动换成你的服务器。

    # 服务器上（GPU 或纯 CPU 都行）
    pip install fastapi uvicorn python-multipart funasr modelscope torch torchaudio
    python asr_openai_server.py --host 127.0.0.1 --port 8001 --device cuda:0

    # 本地验证
    python tools/probe_asr_key.py --base-url http://127.0.0.1:8001/v1 --model sensevoice-local

注意：本文件**没有在本机实测**（本机没装 funasr、也没有可用 CUDA 的 torch）。
它的接口契约与云端那家完全一致，所以"能不能通"取决于 funasr 侧，而不取决于我们的适配器。
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import tempfile
import time

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

LOG = logging.getLogger("citty.local-asr")
API_KEY = os.environ.get("CITTY_ASR_KEY", "").strip()
MODEL_ID = "iic/SenseVoiceSmall"
PUBLIC_NAME = "sensevoice-local"

app = FastAPI(title="citty-local-asr", version="0.1")
_model = None


def load_model(model_id: str, device: str, with_vad: bool) -> None:
    global _model, MODEL_ID
    from funasr import AutoModel  # 延迟导入：没装依赖时不要连服务都起不来

    kwargs = dict(model=model_id, trust_remote_code=True, device=device, disable_update=True)
    if with_vad:
        kwargs["vad_model"] = "fsmn-vad"
    LOG.info("加载模型 %s (device=%s, vad=%s) …", model_id, device, with_vad)
    _model = AutoModel(**kwargs)
    MODEL_ID = model_id


def clean_text(text: str) -> str:
    """SenseVoice 会输出 <|zh|><|NEUTRAL|><|Speech|> 这类富标签，这里剥掉。"""
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        return rich_transcription_postprocess(text)
    except Exception:  # 老版本 funasr 没有这个函数
        return re.sub(r"<\|[^|]*\|>", "", text)


@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [{"id": PUBLIC_NAME, "object": "model", "owned_by": "citty"}]}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=""),
    language: str | None = Form(default=None),
    authorization: str = Header(default=""),
):
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="bad api key")
    if _model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    raw = await file.read()
    suffix = os.path.splitext(file.filename or "a.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(raw)
    tmp.close()

    t0 = time.perf_counter()
    try:
        res = _model.generate(
            input=tmp.name,
            language=(language or "auto"),
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        text = clean_text(res[0]["text"]) if res else ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    LOG.info("转写 %d 字节 → %.1f ms → %s", len(raw), elapsed_ms, text[:60])
    return {"text": text, "elapsed_ms": elapsed_ms, "bytes_in": len(raw), "model": model or PUBLIC_NAME}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="默认只监听本机，靠 SSH 隧道访问，不要裸奔公网")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--model", default="iic/SenseVoiceSmall", help="ModelScope 上的模型 id")
    ap.add_argument("--device", default="auto", help="auto | cpu | cuda:0")
    ap.add_argument("--vad", action="store_true", help="启用 fsmn-vad（长音频一次传时有用）")
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = ap.parse_args()

    device = args.device
    if device == "auto":
        try:
            import torch

            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    load_model(args.model, device, args.vad)
    LOG.info("就绪：http://%s:%d/v1/audio/transcriptions （鉴权键 %s）",
             args.host, args.port, "已启用" if API_KEY else "未启用")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
