"""会话编排：捕获线程 → VAD 切句 → ASR → MT → 修正 pass → 广播。"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Sequence

from .asr import make_asr
from .audio import make_source
from .config import Config
from .hub import Hub
from .mt import make_mt
from .segmenter import make_segmenter
from .types import Segment, SpeechSegment, now_ms, to_srt

log = logging.getLogger("citty.session")


class Session:
    def __init__(self, cfg: Config, hub: Hub):
        self.cfg = cfg
        self.hub = hub
        self.sr = int(cfg.get_path("audio.sample_rate", 16000))
        self.source = make_source(cfg)
        self.segmenter = make_segmenter(cfg)
        self.asr = make_asr(cfg)
        self.mt = make_mt(cfg)

        self.src_lang = str(cfg.get_path("mt.source_lang", "auto"))
        self.tgt_lang = str(cfg.get_path("mt.target_lang", "zh-Hans"))
        self.context_n = int(cfg.get_path("mt.context_sentences", 3))
        self.glossary: dict = dict(cfg.get_path("mt.glossary", {}) or {})
        self.corrector_on = bool(cfg.get_path("mt.corrector.enabled", True))
        self.corrector_delay = float(cfg.get_path("mt.corrector.delay_s", 0.4))
        self.mode = str(cfg.get_path("overlay.mode", "bilingual"))

        self.segments: dict[str, Segment] = {}
        self.order: list[str] = []
        self.stats = {"segments": 0, "translated": 0, "dropped": 0, "errors": 0}
        self._audio_q: queue.Queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._worker: asyncio.Task | None = None
        self._started = False
        # ASR 熔断：key 欠费/断网时不要在几秒内打几百次请求
        self._asr_errors = 0
        self._asr_paused_until = 0.0
        self._asr_reported = False
        self._last_asr_error = ""

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._capture_loop, name="capture", daemon=True).start()
        self._worker = asyncio.create_task(self._worker_loop(), name="pipeline")
        self.publish_session()
        log.info("会话启动: asr=%s mt=%s source=%s", self.asr.name, self.mt.name,
                 self.cfg.get_path("audio.source"))

    async def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.cancel()
        self.source.close()

    # ------------------------------------------------------------------ #
    def _capture_loop(self) -> None:
        try:
            for block in self.source.chunks():
                if self._stop.is_set():
                    break
                for seg in self.segmenter.push(block):
                    self._enqueue(seg)
        except Exception as exc:  # pragma: no cover
            log.exception("捕获线程异常")
            self.hub.publish({"type": "error", "code": "CAPTURE_FAILED", "msg": str(exc)})
        finally:
            for seg in self.segmenter.flush():
                self._enqueue(seg)
            try:
                self._audio_q.put_nowait(None)
            except queue.Full:
                pass

    def _enqueue(self, seg: SpeechSegment) -> None:
        try:
            self._audio_q.put_nowait(seg)
        except queue.Full:  # 落后太多 → 丢最旧的，保实时
            try:
                self._audio_q.get_nowait()
                self.stats["dropped"] += 1
                self._audio_q.put_nowait(seg)
            except Exception:
                pass

    async def _worker_loop(self) -> None:
        while True:
            item = await asyncio.to_thread(self._audio_q.get)
            if item is None:
                break
            try:
                await self._process(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats["errors"] += 1
                log.exception("处理分段失败")
                self.hub.publish({"type": "error", "code": "PIPELINE_FAILED", "msg": str(exc)})

    # ------------------------------------------------------------------ #
    def _asr_failed(self, exc: Exception) -> None:
        """连续失败达阈值就熔断 5 秒：欠费/断网时一个 45s 的测试能刷出 400 条错误。"""
        self._asr_errors += 1
        self._last_asr_error = str(exc).strip().splitlines()[0][:200]
        if self._asr_errors == 1 or self._asr_errors % 5 == 0:
            self.hub.publish({"type": "error", "code": "ASR_FAILED",
                              "msg": self._last_asr_error,
                              "consecutive": self._asr_errors})
        if self._asr_errors >= 3:
            self._asr_paused_until = time.time() + 5.0
            log.warning("ASR 连续失败 %d 次，暂停 5s 再试（最后错误: %s）",
                        self._asr_errors, self._last_asr_error)

    # ------------------------------------------------------------------ #
    async def _process(self, speech: SpeechSegment) -> None:
        seg = Segment(
            id=f"seg-{speech.index:04d}",
            t_start=speech.t_start,
            t_end=speech.t_end,
            src_lang=self.src_lang,
            tgt_lang=self.tgt_lang,
            stability="partial",
        )
        self._register(seg)

        # 1) ASR
        if time.time() < self._asr_paused_until:
            # ASR 刚连续失败过：这段时间直接跳过，别把配额和日志打爆
            self.stats["dropped"] += 1
            seg.stability = "final"
            self._emit(seg)
            return
        try:
            asr = await asyncio.to_thread(self.asr.transcribe, speech.pcm, self.sr, speech.t_start)
            self._asr_errors = 0
        except Exception as exc:
            self.stats["errors"] += 1
            self._asr_failed(exc)
            seg.stability = "final"
            self._emit(seg)
            return

        seg.src = asr.text.strip()
        seg.src_lang = asr.lang or self.src_lang
        seg.latency_ms["asr"] = round(asr.latency_ms, 1)
        seg.stability = "stable"
        self._emit(seg)
        if not seg.src:
            seg.stability = "final"
            self._emit(seg)
            return

        # 2) MT（带前文上下文）
        ctx = self._context_before(seg)
        await self._translate(seg, ctx, final=True)

        # 3) 修正 pass：整句结束后用上下文重译一次，质量更高（同 id 覆盖）
        if self.corrector_on and self.corrector_delay > 0:
            await asyncio.sleep(self.corrector_delay)
            ctx2 = self._context_before(seg, extra=1)
            await self._translate(seg, ctx2, final=True, correction=True)

    async def _translate(self, seg: Segment, ctx: Sequence[str],
                         final: bool = False, correction: bool = False) -> None:
        t0 = now_ms()
        try:
            res = await asyncio.to_thread(
                self.mt.translate, seg.src, seg.src_lang, seg.tgt_lang, ctx, self.glossary
            )
        except Exception as exc:
            self.stats["errors"] += 1
            self.hub.publish({"type": "error", "code": "MT_FAILED", "msg": str(exc)})
            return

        seg.latency_ms["mt"] = round(res.latency_ms, 1)
        seg.latency_ms["total"] = round(seg.latency_ms.get("asr", 0) + res.latency_ms, 1)
        seg.model = res.model
        if final:
            seg.stability = "final"

        new_text = res.text.strip()
        if correction:
            if not new_text or new_text == seg.tgt:
                return  # 修正无变化 → 不打扰前端
            seg.rev += 1
            log.info("修正 %s (rev%d → %dms)", seg.id, seg.rev, res.latency_ms)
        seg.tgt = new_text
        if not correction:
            self.stats["translated"] += 1
        self._emit(seg)

    def _context_before(self, seg: Segment, extra: int = 0) -> list[str]:
        n = self.context_n + extra
        idx = self.order.index(seg.id)
        prev = self.order[max(0, idx - n):idx]
        return [self.segments[i].src for i in prev if self.segments[i].src]

    # ------------------------------------------------------------------ #
    def _register(self, seg: Segment) -> None:
        self.segments[seg.id] = seg
        self.order.append(seg.id)
        self.stats["segments"] += 1
        self._emit(seg)

    def _emit(self, seg: Segment) -> None:
        self.hub.publish({"type": "segment", **seg.to_dict()})

    def publish_session(self) -> None:
        self.hub.publish({
            "type": "session",
            "mode": self.mode,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "asr": self.asr.name,
            "mt": self.mt.name,
            "stats": self.stats,
        })

    def status(self) -> dict:
        return {
            "source": self.cfg.get_path("audio.source"),
            "asr": self.asr.name,
            "mt": self.mt.name,
            "mode": self.mode,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "glossary_size": len(self.glossary),
            "corrector": self.corrector_on,
            "clients": self.hub.connected,
            "stats": self.stats,
        }

    def srt(self, bilingual: bool = True, final_only: bool = True) -> str:
        segs = [self.segments[i] for i in self.order]
        if final_only:
            segs = [s for s in segs if s.stability == "final"]
        return to_srt(segs, bilingual=bilingual)
