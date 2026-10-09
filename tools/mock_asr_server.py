"""一个假的 OpenAI 兼容 ASR 服务，用来在不花配额的前提下验证「真实适配器」这一段。

背景：硅基流动/百炼的 ASR 是付费（或需免费额度）的，但**代码路径**不该等到有额度才验证。
这个服务实现了 /v1/models、/v1/audio/transcriptions、/v1/chat/completions 三个端点，
返回固定句子（并模拟一点网络延迟），于是可以用 config 的 `-o` 覆盖把引擎指过来，
真实地跑一遍：真实音频文件 → VAD 切句 → **真实 ASR 适配器（multipart 上传）** → 真实翻译 → 字幕/SRT。

    python tools/mock_asr_server.py --port 8799
    # 另一个窗口：
    python -m citty.cli -c D:\\Citty\\config.yaml -o asr.openai_compat.base_url=http://127.0.0.1:8799/v1 file out\\samples\\official_en.wav --seconds 25
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENTENCES = [
    "The new chip runs at 3.2 gigahertz and it is remarkably power efficient.",
    "The neural network was trained on a large corpus of subtitles from old movies.",
    "Latency matters more than raw accuracy when you are watching live video.",
    "Carbon fiber is expensive, but it resists cracking much better than steel.",
    "Let's take a look at the benchmark results before we ship this thing.",
]


class Handler(BaseHTTPRequestHandler):
    sentence_idx = 0
    delay_ms = 250
    received_bytes = 0

    def log_message(self, *args):  # 安静一点
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"object": "list", "data": [
                {"id": "mock/asr-small", "object": "model"},
                {"id": "mock/asr-turbo", "object": "model"},
            ]})
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        Handler.received_bytes += len(raw)
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000.0)

        text = SENTENCES[Handler.sentence_idx % len(SENTENCES)]
        Handler.sentence_idx += 1
        print(f"  [mock-asr] +{len(raw)}B → {text[:60]}", flush=True)

        if self.path.endswith("/audio/transcriptions"):
            self._send({"text": text})
        elif self.path.endswith("/chat/completions"):
            self._send({"choices": [{"message": {"role": "assistant", "content": text}}]})
        else:
            self._send({"error": {"message": "not found"}}, 404)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--delay-ms", type=int, default=250, help="模拟网络+推理延迟")
    args = ap.parse_args()
    Handler.delay_ms = args.delay_ms

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock ASR 服务已启动: http://127.0.0.1:{args.port}/v1  (Ctrl+C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(f"\n共收到 {Handler.received_bytes} 字节音频，{Handler.sentence_idx} 次请求")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
