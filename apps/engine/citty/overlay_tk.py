"""桌面字幕窗（Tkinter，置顶 + 无边框 + 可拖拽）。

不用 Electron/WebView：Tkinter 随 Python 自带，零安装、启动快、常驻内存小。
用 `python -m citty.cli overlay` 启动，连接引擎的 WebSocket。
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import tkinter as tk

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover
    websocket = None


class OverlayApp:
    def __init__(self, ws_url: str, font: str, tgt_size: int, src_size: int,
                 width_pct: float, bottom_margin: int, alpha: float, mode: str,
                 click_through_bg: str = "#0b0b0e"):
        self.ws_url = ws_url
        self.mode = mode
        self.queue: "queue.Queue[dict]" = queue.Queue()
        self.connected = False

        self.root = tk.Tk()
        self.root.title("Citty 字幕层")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        bg = click_through_bg
        self.root.configure(bg=bg)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.width = int(sw * width_pct)
        self.bottom_margin = bottom_margin
        self.screen = (sw, sh)

        self.frame = tk.Frame(self.root, bg=bg)
        self.frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.tgt_label = tk.Label(
            self.frame, text="", font=(font, tgt_size, "bold"), fg="#ffffff",
            bg="#0b0b0e", wraplength=int(self.width * 0.94), justify="center",
            padx=18, pady=10,
        )
        self.src_label = tk.Label(
            self.frame, text="", font=(font, src_size), fg="#c9d1d9",
            bg="#15161a", wraplength=int(self.width * 0.94), justify="center",
            padx=14, pady=6,
        )
        self.tgt_label.pack(fill="x")
        self.src_label.pack(fill="x", pady=(4, 0))

        self.status = tk.Label(self.frame, text="connecting…", font=(font, 9),
                               fg="#7d8590", bg="#0b0b0e", anchor="e")
        self.status.pack(fill="x", pady=(2, 4))

        self._place_initial()
        self._bind_events()
        self._start_ws()
        self.root.after(40, self._pump)
        self._last_update = 0.0

    # ------------------------------------------------------------------ #
    def _place_initial(self) -> None:
        sw, sh = self.screen
        x = (sw - self.width) // 2
        self.root.geometry(f"{self.width}x120+{x}+{sh - self.bottom_margin - 120}")

    def _bind_events(self) -> None:
        for w in (self.root, self.frame, self.tgt_label, self.src_label):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", self._menu)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="双语（默认）", command=lambda: self._set_mode("bilingual"))
        self.menu.add_command(label="仅译文", command=lambda: self._set_mode("tgt"))
        self.menu.add_command(label="仅原文", command=lambda: self._set_mode("src"))
        self.menu.add_separator()
        self.menu.add_command(label="重连", command=self._reconnect)
        self.menu.add_command(label="退出", command=self.root.destroy)

    def _drag_start(self, e) -> None:
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, e) -> None:
        if not getattr(self, "_drag", None):
            return
        x0, y0, wx, wy = self._drag
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _menu(self, e) -> None:
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------------ #
    def _send(self, payload: dict) -> None:
        if self.ws:
            try:
                self.ws.send(json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._send({"type": "set_mode", "mode": mode})
        self._render_last()

    def _reconnect(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self._start_ws()

    # ------------------------------------------------------------------ #
    def _start_ws(self) -> None:
        self.ws = None
        self._last = {"src": "", "tgt": ""}
        if websocket is None:
            self.status.config(text="websocket-client 未安装")
            return

        def run():
            while True:
                try:
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_open=lambda ws: self.queue.put({"type": "__open"}),
                        on_message=lambda ws, m: self.queue.put(json.loads(m)),
                        on_error=lambda ws, e: self.queue.put({"type": "__error", "msg": str(e)}),
                        on_close=lambda ws, *a: self.queue.put({"type": "__close"}),
                    )
                    self.ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as exc:  # pragma: no cover
                    self.queue.put({"type": "__error", "msg": str(exc)})
                import time as _t
                _t.sleep(1.5)  # 断了自动重连

        threading.Thread(target=run, name="ws", daemon=True).start()
        self.status.config(text=f"connecting {self.ws_url}")

    def _pump(self) -> None:
        try:
            while True:
                msg = self.queue.get_nowait()
                t = msg.get("type")
                if t == "__open":
                    self.connected = True
                    self.status.config(text="connected")
                elif t == "__close":
                    self.connected = False
                    self.status.config(text="reconnecting…")
                elif t == "__error":
                    self.status.config(text=f"error: {msg.get('msg', '')[:60]}")
                elif t == "session":
                    self.mode = msg.get("mode", self.mode)
                    self._last = {"src": "", "tgt": ""}
                    self.status.config(text=f"{msg.get('asr')} → {msg.get('mt')}  ·  {self.mode}")
                elif t == "segment":
                    self._last = {"src": msg.get("src", ""), "tgt": msg.get("tgt", "")}
                    self._render_last(msg.get("latency_ms", {}))
                elif t == "error":
                    self.status.config(text=f"engine error: {msg.get('code')}")
        except queue.Empty:
            pass
        self.root.after(40, self._pump)

    def _render_last(self, latency: dict | None = None) -> None:
        src, tgt = self._last.get("src", ""), self._last.get("tgt", "")
        show_tgt = self.mode in ("tgt", "bilingual") and bool(tgt)
        show_src = self.mode in ("src", "bilingual") and bool(src)
        self.tgt_label.config(text=tgt if show_tgt else "")
        self.src_label.config(text=src if show_src else "")
        self.tgt_label.pack_configure(fill="x") if show_tgt else self.tgt_label.pack_forget()
        if show_tgt:
            if not self.tgt_label.winfo_manager():
                self.tgt_label.pack(fill="x", before=self.src_label)
        if show_src:
            if not self.src_label.winfo_manager():
                self.src_label.pack(fill="x", pady=(4, 0))
        else:
            self.src_label.pack_forget()
        if latency:
            self.status.config(text=f"端到端 ≈{latency.get('total', 0):.0f}ms "
                                    f"(asr {latency.get('asr', 0):.0f} / mt {latency.get('mt', 0):.0f})")
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        self.root.update_idletasks()
        h = max(60, self.frame.winfo_reqheight())
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if y < 0:
            sh = self.root.winfo_screenheight()
            y = sh - self.bottom_margin - h
        self.root.geometry(f"{self.width}x{h}+{x}+{y}")

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    from .config import load_config

    cfg = load_config()
    ap = argparse.ArgumentParser(description="Citty 桌面字幕窗")
    ap.add_argument("--ws", default=None, help="引擎 WebSocket 地址")
    ap.add_argument("--mode", default=None, choices=["src", "tgt", "bilingual"])
    args = ap.parse_args(argv)

    host = cfg.get_path("server.host", "127.0.0.1")
    port = cfg.get_path("server.port", 8756)
    ws_url = args.ws or f"ws://{host}:{port}/ws"
    ov = cfg.get("overlay", {}) or {}

    app = OverlayApp(
        ws_url=ws_url,
        font=str(ov.get("font_family", "Microsoft YaHei UI")),
        tgt_size=int(ov.get("tgt_font_size", 26)),
        src_size=int(ov.get("src_font_size", 16)),
        width_pct=float(ov.get("width_pct", 0.62)),
        bottom_margin=int(ov.get("bottom_margin_px", 96)),
        alpha=float(ov.get("alpha", 0.92)),
        mode=args.mode or str(ov.get("mode", "bilingual")),
    )
    app.run()
    return 0
