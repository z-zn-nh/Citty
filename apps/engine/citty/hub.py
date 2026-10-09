"""事件广播：会话产出的事件分发给所有前端（WS 客户端 / 控制台）。"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("citty.hub")


class Hub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self.connected = 0

    def attach(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues.add(q)
        self.connected += 1
        return q

    def detach(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)
        self.connected = max(0, self.connected - 1)

    def publish(self, event: dict) -> None:
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:  # 丢最旧的一条，保证实时性
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    dead.append(q)
        for q in dead:
            self.detach(q)


class ConsoleHub(Hub):
    """无界面自测用：把事件打到 stdout。"""

    def __init__(self, show: set[str] | None = None) -> None:
        super().__init__()
        self.show = show or {"segment", "status", "error", "session"}

    def publish(self, event: dict) -> None:
        super().publish(event)
        if event.get("type") not in self.show:
            return
        if event["type"] == "segment":
            stab = event.get("stability", "")
            src = event.get("src", "")
            tgt = event.get("tgt", "")
            lat = event.get("latency_ms", {})
            tag = {"partial": "…", "stable": "▸", "final": "✔"}.get(stab, "?")
            ms = f"[{lat.get('total', 0):.0f}ms]" if lat.get("total") else ""
            print(f"{tag} {event['t_start']:7.2f}-{event['t_end']:7.2f} rev{event.get('rev')} {ms}")
            if src:
                print(f"   EN: {src}")
            if tgt:
                print(f"   ZH: {tgt}")
        else:
            print(json.dumps(event, ensure_ascii=False))
