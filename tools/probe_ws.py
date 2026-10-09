"""验证前端协议：连上引擎 WebSocket，收 15 秒事件并校验字段。

    python tools/probe_ws.py [--seconds 15] [--url ws://127.0.0.1:8756/ws]
"""
from __future__ import annotations

import argparse
import json
import time

import websocket


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8756/ws")
    ap.add_argument("--seconds", type=float, default=15.0)
    args = ap.parse_args()

    events: list[dict] = []
    ws = websocket.create_connection(args.url, timeout=10)
    print(f"已连接 {args.url}")
    deadline = time.time() + args.seconds
    ws.settimeout(2.0)
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception:
            continue
        if not raw:
            continue
        msg = json.loads(raw)
        events.append(msg)
        if msg.get("type") == "session":
            print(f"session: asr={msg.get('asr')} mt={msg.get('mt')} "
                  f"{msg.get('src_lang')} → {msg.get('tgt_lang')}")
        elif msg.get("type") == "segment":
            print(f"segment {msg['id']} rev{msg.get('rev')} [{msg.get('stability')}] "
                  f"{msg.get('t_start', 0):.2f}-{msg.get('t_end', 0):.2f} "
                  f"lat={msg.get('latency_ms')}")
            print(f"   src: {msg.get('src')}")
            print(f"   tgt: {msg.get('tgt')}")
        else:
            print(json.dumps(msg, ensure_ascii=False))
    ws.close()

    segs = [e for e in events if e.get("type") == "segment"]
    finals = [e for e in segs if e.get("stability") == "final" and e.get("tgt")]
    required = {"id", "rev", "t_start", "t_end", "src", "tgt", "stability", "latency_ms", "tgt_lang"}
    missing = [m for e in segs if (m := sorted(required - set(e)))]
    print("-" * 60)
    print(f"事件 {len(events)} 条，segment {len(segs)} 条，带译文的 final {len(finals)} 条")
    if missing:
        print(f"❌ 字段缺失: {missing[:2]}")
        return 1
    if not finals:
        print("⚠️  期间没有 final 字幕（可能刚好没有说话声）")
        return 0
    print("✅ 协议校验通过（字段齐全、有译文）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
