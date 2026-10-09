"""领域路由的**契约层**：接口、结果类型、后端工厂。

拆成两个文件是有意的：
  · `router.py`     —— 本文件。`RouterBackend` 接口 + `DomainDecision` + `make_router`
  · `heuristic.py`  —— 关键词打分的实现（本阶段唯一的后端）
  · 以后 P3-b 加 `jev.py` / `llm.py` 等决策模型时，各自一个文件，接口不变。

设计前提（P3-a）：**不改变任何翻译行为**。它只回答一个问题——"这段视频大概是什么领域？"，
把答案连同证据写进 `Segment.domain` 与路由日志；攒够真实分布之后，P3-b 才用它决定
"哪个领域用哪个翻译模型"。在没有评测集和真实分布之前就按领域切模型，等于用随机数
替换一个至少不会更差的默认模型。

后端一览：
  · `none`      关掉埋点（不判断、不写日志，零开销）
  · `heuristic` 关键词 + 元数据打分（零模型、零显存、微秒级）
  · 以后        JEV / 小模型做决策，实现同一个 `decide()` 即可接进来
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("citty.router")

DEFAULT_DOMAIN = "default"


@dataclass
class DomainDecision:
    """一次领域判断的完整结果（含证据，便于事后复盘为什么这么判）。"""

    domain: str = DEFAULT_DOMAIN
    stable: str = DEFAULT_DOMAIN      # 经迟滞后的稳定判定（P3-a 只记录）
    confidence: float = 0.0
    evidence: str = ""                # 命中的关键词，如 "friend, shopping, 朋友"
    scores: dict = field(default_factory=dict)
    backend: str = ""
    cost_ms: float = 0.0              # 埋点自身耗时（用来证明"没引入可感知延迟"）

    def to_dict(self) -> dict:
        return {
            "domain": self.domain, "stable": self.stable, "confidence": self.confidence,
            "evidence": self.evidence, "backend": self.backend, "cost_ms": self.cost_ms,
        }


class RouterBackend:
    """领域判断的统一接口。

    `decide()` 必须做到两件事：**不抛异常**、**不阻塞**（实时链路里它在关键路径上）。
    实现方自己决定是有状态（累积正文、迟滞）还是无状态。
    """

    name = "base"

    def decide(self, text: str, meta: dict | None = None) -> DomainDecision:
        raise NotImplementedError

    def reset(self) -> None:
        """新的会话开始时调用。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name}>"


class NoopRouter(RouterBackend):
    """完全关掉埋点：不判断、不记录。"""

    name = "none"

    def decide(self, text: str, meta: dict | None = None) -> DomainDecision:
        return DomainDecision(domain="unknown", stable="unknown", confidence=0.0,
                              evidence="", backend=self.name, cost_ms=0.0)


def make_router(cfg) -> RouterBackend:
    """按 `router.backend` 造后端。"""
    from .heuristic import _META_WEIGHT, HeuristicRouter  # 延迟导入：避免与契约层循环依赖

    backend = str(cfg.get_path("router.backend", "heuristic")).lower()
    if backend in ("none", "off", "disabled"):
        return NoopRouter()
    if backend != "heuristic":
        log.warning("未知的 router.backend=%s，回退到 heuristic", backend)
    return HeuristicRouter(
        meta_weight=float(cfg.get_path("router.meta_weight", _META_WEIGHT)),
        hysteresis=int(cfg.get_path("router.hysteresis", 2)),
        # 兜底值必须和 config.yaml 里写的一致，否则会出现「配置说 0.50、实际跑 0.30」
        min_confidence=float(cfg.get_path("router.min_confidence", 0.50)),
        history_chars=int(cfg.get_path("router.history_chars", 4000)),
        extra_keywords=dict(cfg.get_path("router.extra_keywords", {}) or {}),
    )
