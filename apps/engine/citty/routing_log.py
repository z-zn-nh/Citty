"""P3-a：路由埋点的落盘与汇总。

跑两周攒数据靠的就是这个模块。两条硬要求：

1. **每写一行就 flush** —— 引擎是被 `process.kill()` 硬杀的（长测就是这么杀的），
   缓冲区里留着的数据等于没有。所以用行级 JSONL + 立即 flush，宁可多一点 IO。
2. **一次会话一个文件，第一行是表头** —— 表头记下这次会话用的 ASR/MT 后端、语言对、
   以及喂进来的元数据（标题/UP主），否则以后拿到一堆日志也不知道是哪个模型产的。

文件位置：`<out_dir>/routing/<session_id>.jsonl`（该目录在 .gitignore 里，不进仓库）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("citty.routing")

SCHEMA = 1


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


@dataclass
class RoutingRecord:
    """一条分段记录。字段名对齐路线图要求（domain_guess / asr_text / mt_text /
    model_used / latency / revisions），其余是让分析能落到实处的补充项。"""

    type: str = "segment"
    session_id: str = ""
    seq: int = 0
    at: str = ""
    idx: int = 0                     # 第几段（同一段会因修正 pass 出现多行）
    seg_id: str = ""
    rev: int = 1                     # 修正次数（rev>1 说明这句被重译过）
    phase: str = ""                  # asr_only | mt | correction
    t_start: float = 0.0
    t_end: float = 0.0
    dur_s: float = 0.0
    src_lang: str = ""
    tgt_lang: str = ""
    domain_guess: str = ""
    domain_stable: str = ""
    domain_confidence: float = 0.0
    domain_evidence: str = ""
    domain_scores: dict = field(default_factory=dict)
    router_ms: float = 0.0
    asr_text: str = ""
    mt_text: str = ""
    model_used: str = ""
    latency: dict = field(default_factory=dict)
    stability: str = ""
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class RoutingLog:
    """JSONL 写入器。`enabled=False` 时所有方法都是空操作（零开销）。"""

    def __init__(self, cfg, session_id: str | None = None,
                 subdir: str = "routing", enabled: bool | None = None):
        self.session_id = session_id or str(cfg.get_path("session.id", "") or "") or new_session_id()
        if enabled is None:
            enabled = bool(cfg.get_path("router.log.enabled", True)) and \
                str(cfg.get_path("router.backend", "heuristic")).lower() not in ("none", "off", "disabled")
        self.enabled = bool(enabled)
        out_dir = Path(str(cfg.get_path("_out_dir", "out")))
        self.path = out_dir / subdir / f"{self.session_id}.jsonl"
        self._fh = None
        self._seq = 0
        self.records = 0

    # ------------------------------------------------------------------ #
    def open_session(self, session) -> None:
        """写表头：这次会话到底用的什么配置，否则日志事后没法解释。"""
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
            header = {
                "type": "header", "schema": SCHEMA, "session_id": self.session_id,
                "at": _now_iso(), "asr": getattr(session.asr, "name", "?"),
                "mt": getattr(session.mt, "name", "?"),
                "router": getattr(session.router, "name", "?"),
                "src_lang": session.src_lang, "tgt_lang": session.tgt_lang,
                "corrector": session.corrector_on, "glossary_size": len(session.glossary),
                "meta": dict(getattr(session, "meta", {}) or {}),
            }
            self._fh.write(json.dumps(header, ensure_ascii=False) + "\n")
            self._fh.flush()
            log.info("路由埋点写入: %s", self.path)
        except Exception:
            log.exception("路由日志打开失败，埋点自动关闭")
            self.enabled = False
            self._fh = None

    def write(self, seg, decision, phase: str = "mt", stats: dict | None = None) -> None:
        if not self.enabled or self._fh is None:
            return
        self._seq += 1
        rec = RoutingRecord(
            session_id=self.session_id, seq=self._seq, at=_now_iso(), idx=_idx_of(seg),
            seg_id=seg.id, rev=seg.rev, phase=phase,
            t_start=round(seg.t_start, 3), t_end=round(seg.t_end, 3),
            dur_s=round(max(0.0, seg.t_end - seg.t_start), 3),
            src_lang=seg.src_lang, tgt_lang=seg.tgt_lang,
            domain_guess=getattr(decision, "domain", ""),
            domain_stable=getattr(decision, "stable", ""),
            domain_confidence=getattr(decision, "confidence", 0.0),
            domain_evidence=getattr(decision, "evidence", ""),
            domain_scores=getattr(decision, "scores", {}) or {},
            router_ms=getattr(decision, "cost_ms", 0.0),
            asr_text=seg.src, mt_text=seg.tgt, model_used=seg.model or "",
            latency=dict(seg.latency_ms or {}), stability=seg.stability,
            stats=dict(stats or {}),
        )
        try:
            self._fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
            self._fh.flush()
            self.records += 1
        except Exception:
            log.exception("路由日志写入失败，埋点自动关闭")
            self.enabled = False

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        return {"session_id": self.session_id, "enabled": self.enabled,
                "records": self.records, "path": str(self.path) if self.enabled else ""}


def _idx_of(seg) -> int:
    try:
        return int(str(seg.id).rsplit("-", 1)[-1])
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# 读取与汇总（`citty routes` 的数据源）
# --------------------------------------------------------------------------- #
def iter_records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 1)


def summarize(dir_path: str | Path, session: str | None = None) -> dict:
    """把 JSONL 汇总成一份能直接看/能直接喂给 P3-b 的统计。

    同一段可能有多行（第一次翻译 + 修正 pass），统计时**按 idx 取 rev 最大的一行**，
    因为那才是这段字幕最终呈现的样子；`revisions` 则反映被改过多少次。
    """
    d = Path(dir_path)
    files = sorted([p for p in d.glob("*.jsonl") if p.is_file()]) if d.exists() else []
    if session:
        files = [p for p in files if session in p.name]

    sessions: list[dict] = []
    domain_records: dict[str, int] = {}
    domain_stats: dict[str, dict] = {}
    model_counts: dict[str, int] = {}
    lang_pairs: dict[str, int] = {}
    asr_lat: list[float] = []
    mt_lat: list[float] = []
    tot_lat: list[float] = []
    router_lat: list[float] = []
    rev_hist: dict[int, int] = {}
    total_lines = 0
    total_segments = 0

    for f in files:
        header: dict = {}
        best: dict[int, dict] = {}
        switches = 0
        last_stable = None
        final_stats: dict = {}
        for rec in iter_records(f):
            total_lines += 1
            if rec.get("type") == "header":
                header = rec
                continue
            idx = int(rec.get("idx", 0))
            cur = best.get(idx)
            if cur is None or int(rec.get("rev", 1)) > int(cur.get("rev", 1)):
                best[idx] = rec
            st = rec.get("domain_stable")
            if st and st != last_stable:
                switches += 1
                last_stable = st
            if isinstance(rec.get("stats"), dict) and rec["stats"]:
                final_stats = rec["stats"]

        total_segments += len(best)
        sess_domains: dict[str, int] = {}
        for rec in best.values():
            dom = rec.get("domain_stable") or rec.get("domain_guess") or "unknown"
            sess_domains[dom] = sess_domains.get(dom, 0) + 1
            domain_records[dom] = domain_records.get(dom, 0) + 1
            st = domain_stats.setdefault(dom, {"n": 0, "rev_gt1": 0, "total_ms": [], "chars": 0})
            st["n"] += 1
            if int(rec.get("rev", 1)) > 1:
                st["rev_gt1"] += 1
            lat = rec.get("latency") or {}
            if isinstance(lat, dict) and lat.get("total"):
                st["total_ms"].append(float(lat["total"]))
            st["chars"] += len(rec.get("asr_text") or "")
            for k, bucket in (("asr", asr_lat), ("mt", mt_lat), ("total", tot_lat)):
                v = (lat or {}).get(k)
                if v:
                    bucket.append(float(v))
            if rec.get("router_ms"):
                router_lat.append(float(rec["router_ms"]))
            if rec.get("model_used"):
                model_counts[rec["model_used"]] = model_counts.get(rec["model_used"], 0) + 1
            pair = f"{rec.get('src_lang','?')}→{rec.get('tgt_lang','?')}"
            lang_pairs[pair] = lang_pairs.get(pair, 0) + 1
            r = int(rec.get("rev", 1))
            rev_hist[r] = rev_hist.get(r, 0) + 1

        top = sorted(sess_domains.items(), key=lambda kv: -kv[1])[:3]
        sessions.append({
            "file": f.name, "session_id": header.get("session_id", f.stem),
            "at": header.get("at", ""), "asr": header.get("asr", ""), "mt": header.get("mt", ""),
            "router": header.get("router", ""), "src_lang": header.get("src_lang", ""),
            "tgt_lang": header.get("tgt_lang", ""), "meta": header.get("meta", {}),
            "segments": len(best), "lines": sum(1 for _ in iter_records(f)),
            "domains": sess_domains, "top_domains": top, "domain_switches": switches,
            "stats": final_stats,
        })

    return {
        "files": len(files), "lines": total_lines, "segments": total_segments,
        "sessions": sessions,
        "domains": dict(sorted(domain_records.items(), key=lambda kv: -kv[1])),
        "domain_table": {k: {"n": v["n"], "rev_gt1": v["rev_gt1"],
                             "avg_total_ms": round(sum(v["total_ms"]) / len(v["total_ms"]), 1)
                             if v["total_ms"] else 0.0,
                             "chars": v["chars"]} for k, v in
                         sorted(domain_stats.items(), key=lambda kv: -kv[1]["n"])},
        "models": model_counts, "lang_pairs": lang_pairs,
        "latency": {"asr_p50": _pct(asr_lat, .5), "asr_p95": _pct(asr_lat, .95),
                    "mt_p50": _pct(mt_lat, .5), "mt_p95": _pct(mt_lat, .95),
                    "total_p50": _pct(tot_lat, .5), "total_p95": _pct(tot_lat, .95)},
        "router_cost_ms": {"p50": _pct(router_lat, .5), "p95": _pct(router_lat, .95),
                           "max": round(max(router_lat), 3) if router_lat else 0.0,
                           "n": len(router_lat)},
        "revisions": dict(sorted(rev_hist.items())),
    }


def format_report(s: dict) -> str:
    out: list[str] = []
    if not s["files"]:
        return "没有找到路由日志（`router.log.enabled: true` 且跑过一次会话后才有）。"

    out.append("=" * 72)
    out.append(f"路由埋点汇总 · {s['files']} 个会话文件 / {s['lines']} 行 / {s['segments']} 个分段")
    out.append("=" * 72)

    for sess in s["sessions"]:
        out.append(f"\n▸ {sess['file']}")
        out.append(f"    会话 {sess['session_id']}  起于 {sess['at']}")
        out.append(f"    ASR={sess['asr']}  MT={sess['mt']}  router={sess['router']}  "
                   f"语言 {sess['src_lang']}→{sess['tgt_lang']}")
        if sess["meta"]:
            out.append(f"    元数据 {sess['meta']}")
        top = "  ".join(f"{d}({n})" for d, n in sess["top_domains"]) or "—"
        out.append(f"    分段 {sess['segments']}   领域分布 {top}   领域切换 {sess['domain_switches']} 次")
        if sess["stats"]:
            out.append(f"    引擎统计 {sess['stats']}")

    out.append("\n" + "-" * 72)
    out.append("领域分布（所有会话合计）")
    total = sum(s["domains"].values()) or 1
    for dom, n in s["domains"].items():
        bar = "█" * max(1, int(round(n / total * 40)))
        out.append(f"    {dom:<10} {n:>5}  {n / total * 100:5.1f}%  {bar}")

    out.append("\n" + "-" * 72)
    out.append("领域 × 质量（P3-b 要拿这个决定分模型）")
    out.append(f"    {'领域':<10}{'分段':>6}{'被修正句':>9}{'修正率':>8}{'平均总延迟':>11}{'字数':>8}")
    for dom, t in s["domain_table"].items():
        rate = (t["rev_gt1"] / t["n"] * 100) if t["n"] else 0.0
        out.append(f"    {dom:<10}{t['n']:>6}{t['rev_gt1']:>9}{rate:>7.1f}%"
                   f"{t['avg_total_ms']:>10.0f}ms{t['chars']:>8}")

    out.append("\n" + "-" * 72)
    lat = s["latency"]
    out.append(f"延迟   ASR p50 {lat['asr_p50']}ms / p95 {lat['asr_p95']}ms    "
               f"MT p50 {lat['mt_p50']}ms / p95 {lat['mt_p95']}ms    "
               f"端到端 p50 {lat['total_p50']}ms / p95 {lat['total_p95']}ms")
    rc = s["router_cost_ms"]
    out.append(f"埋点自身开销 p50 {rc['p50']}ms / p95 {rc['p95']}ms / 最大 {rc['max']}ms"
               f"（n={rc['n']}）← 这条用来证明「埋点没引入可感知延迟」")
    out.append(f"模型使用 {s['models'] or '—'}")
    out.append(f"语言对   {s['lang_pairs'] or '—'}")
    out.append(f"rev 分布 {s['revisions']}（rev>1 = 被修正 pass 重译过的句子数）")
    return "\n".join(out)
