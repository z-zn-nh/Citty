"""FastAPI 服务：WebSocket 广播字幕 + 浏览器/OBS 用 overlay 页 + SRT 下载。"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse

from .config import Config, load_config
from .hub import Hub
from .session import Session

log = logging.getLogger("citty.server")

OVERLAY_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Citty 字幕层</title>
<style>
  html,body{margin:0;height:100%;background:transparent;overflow:hidden;
            font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;}
  #wrap{position:fixed;left:50%;transform:translateX(-50%);bottom:8%;
        display:flex;flex-direction:column;align-items:center;gap:6px;
        pointer-events:none;text-align:center;max-width:86vw;}
  #tgt{font-size:34px;line-height:1.35;color:#fff;background:rgba(12,12,14,.72);
       padding:12px 20px;border-radius:12px;
       text-shadow:0 2px 6px rgba(0,0,0,.9);white-space:pre-wrap;}
  #src{font-size:20px;line-height:1.3;color:#d7dbe0;background:rgba(12,12,14,.55);
       padding:6px 14px;border-radius:10px;
       text-shadow:0 1px 4px rgba(0,0,0,.9);white-space:pre-wrap;}
  #bar{position:fixed;top:6px;right:8px;font:12px/1.6 monospace;color:#9aa3ad;
       background:rgba(0,0,0,.5);padding:4px 8px;border-radius:6px;}
  #hint{position:fixed;left:50%;transform:translateX(-50%);bottom:8%;
        font-size:22px;color:#e6edf3;background:rgba(12,12,14,.72);
        padding:12px 20px;border-radius:12px;}
  .hidden{display:none}
</style></head><body>
  <div id="wrap"><div id="tgt" class="hidden"></div><div id="src" class="hidden"></div></div>
  <div id="hint">Citty 字幕层：已连接，等待字幕…</div>
  <div id="bar">connecting…</div>
<script>
const tgt=document.getElementById('tgt'), src=document.getElementById('src'), bar=document.getElementById('bar'), hint=document.getElementById('hint');
let mode='bilingual';
function render(s){
  tgt.textContent=s.tgt||''; src.textContent=s.src||'';
  tgt.className = (mode!=='src' && s.tgt) ? '' : 'hidden';
  src.className = (mode!=='tgt' && s.src) ? '' : 'hidden';
  if(mode==='tgt' && !s.tgt) tgt.className='hidden';
  if(s.tgt||s.src) hint.className='hidden';
}
function connect(){
  const ws=new WebSocket(`ws://${location.host}/ws`);
  ws.onopen=()=>{bar.textContent='connected';};
  ws.onclose=()=>{bar.textContent='reconnecting…';setTimeout(connect,1000);};
  ws.onmessage=(e)=>{const m=JSON.parse(e.data);
    if(m.type==='session'){mode=m.mode||'bilingual';bar.textContent=`${m.asr} → ${m.mt}  ${m.mode}`;}
    if(m.type==='segment'){render(m);}
  };
}
connect();
</script></body></html>
"""


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()

    hub = Hub()
    session = Session(cfg, hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await session.start()
        try:
            yield
        finally:
            await session.stop()

    app = FastAPI(title="Citty Realtime Subtitle Engine", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.session = session
    app.state.hub = hub

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return OVERLAY_HTML

    @app.get("/overlay", response_class=HTMLResponse)
    async def overlay():
        return OVERLAY_HTML

    @app.get("/api/status")
    async def status():
        return JSONResponse(session.status())

    @app.get("/api/srt", response_class=PlainTextResponse)
    async def srt(bilingual: bool = True):
        return PlainTextResponse(session.srt(bilingual=bilingual),
                                 headers={"Content-Disposition": 'attachment; filename="citty.srt"'})

    @app.post("/api/mode")
    async def set_mode(payload: dict):
        mode = str(payload.get("mode", "bilingual"))
        if mode not in {"src", "tgt", "bilingual"}:
            return JSONResponse({"ok": False, "error": "mode 必须是 src|tgt|bilingual"}, status_code=400)
        session.mode = mode
        session.publish_session()
        return {"ok": True, "mode": mode}

    @app.post("/api/glossary")
    async def set_glossary(payload: dict):
        session.glossary = {str(k): str(v) for k, v in (payload or {}).items()}
        return {"ok": True, "size": len(session.glossary)}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = hub.attach()
        log.info("客户端接入 (clients=%d)", hub.connected)
        try:
            await websocket.send_text(json.dumps({
                "type": "session", "mode": session.mode, "src_lang": session.src_lang,
                "tgt_lang": session.tgt_lang, "asr": session.asr.name, "mt": session.mt.name,
            }, ensure_ascii=False))
            # 补发最近的字幕，后连的窗口不会空着
            for sid in session.order[-5:]:
                await websocket.send_text(json.dumps(
                    {"type": "segment", **session.segments[sid].to_dict()}, ensure_ascii=False))

            async def pump():
                while True:
                    event = await q.get()
                    await websocket.send_text(json.dumps(event, ensure_ascii=False))

            async def receive():
                while True:
                    raw = await websocket.receive_text()
                    try:
                        cmd = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if cmd.get("type") == "set_mode":
                        session.mode = cmd.get("mode", session.mode)
                        session.publish_session()

            await asyncio.gather(pump(), receive())
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover
            log.info("客户端断开: %s", exc)
        finally:
            hub.detach(q)
            log.info("客户端离开 (clients=%d)", hub.connected)

    return app
