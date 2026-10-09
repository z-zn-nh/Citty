"""P3-a：领域路由（只判断、不改行为）。

设计前提：**这个阶段不改变任何翻译行为**。它只回答一个问题——
"这段视频大概是什么领域？"，并把答案连同证据记录到 `Segment.domain` 和路由日志里。
攒够真实分布之后，P3-b 才拿这些数据去决定"哪个领域用哪个翻译模型"。

为什么先做埋点：路由的收益必须用**数据**证明（"换模型到底有没有更好"），
在没有评测集和真实分布之前做切换，等于用随机数替换默认模型。

三类后端：
  · `none`      关掉埋点（完全不写日志，零开销）
  · `heuristic` 关键词 + 元数据打分（本阶段唯一的实现，零模型、零显存、微秒级）
  · 以后        JEV-9B 之类的决策模型，同一个 `RouterBackend.decide()` 接口接进来

关于"迟滞"（hysteresis）：一次误判不该让领域来回跳，所以连续 N 次猜中同一个领域才改判。
P3-a 里它只影响**记录**哪一次是"稳定判定"，不影响翻译。P3-b 才让它真正参与路由。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("citty.router")

# --------------------------------------------------------------------------- #
# 领域表：8~16 个（P3-b 的 JEV 选择项要求这个量级）。关键词中英双语都收，
# 因为源语言可能是中文也可能是英文；短语比单词更有区分度（见 _PHRASE_BONUS）。
# --------------------------------------------------------------------------- #
DOMAINS: list[tuple[str, str, tuple[str, ...]]] = [
    ("tech", "科技数码", (
        "iphone", "android", "cpu", "gpu", "nvidia", "apple", "google", "microsoft",
        "software", "coding", "programming", "github", "linux", "python", "javascript",
        "ai model", "artificial intelligence", "chatgpt", "algorithm", "database",
        "芯片", "处理器", "显卡", "手机", "参数", "评测", "跑分", "编程", "代码", "算法",
        "人工智能", "大模型", "开源", "软件", "硬件", "装机", "数码", "发布会")),
    ("academic", "学术科普", (
        "research", "paper", "study", "hypothesis", "experiment", "theory", "physics",
        "chemistry", "biology", "quantum", "neuroscience", "statistical", "peer-reviewed",
        "研究", "论文", "实验", "假设", "理论", "物理", "化学", "生物", "量子", "神经",
        "统计", "学术", "文献", "科普")),
    ("medical", "医学健康", (
        "symptom", "diagnosis", "patient", "treatment", "dose", "clinical", "surgery",
        "blood pressure", "infection", "immune", "vaccine", "therapy", "cancer",
        "症状", "症状", "诊断", "患者", "治疗", "剂量", "临床", "手术", "血压", "感染",
        "免疫", "疫苗", "癌", "医生", "健康", "养生", "用药")),
    ("finance", "财经商业", (
        "stock", "market", "investor", "revenue", "profit", "inflation", "interest rate",
        "valuation", "earnings", "ipo", "startup", "funding", "economy", "gdp",
        "股票", "股市", "投资", "营收", "利润", "通胀", "利率", "估值", "财报", "上市",
        "融资", "经济", "房价", "理财", "基金", "商业")),
    ("education", "教学课程", (
        "lesson", "lecture", "course", "homework", "exam", "chapter", "textbook",
        "考试", "课程", "上课", "老师", "同学", "作业", "课本", "章节", "知识点", "讲解",
        "例题", "复习", "期末", "高考", "真题")),
    ("language", "语言学习", (
        "listening", "vocabulary", "pronunciation", "grammar", "phrase", "english",
        "sentence", "repeat after me", "听力", "口语", "语法", "单词", "词汇", "发音",
        "英语", "例句", "跟读", "背单词", "口语练习", "保母级", "保姆级")),
    ("anime", "番剧动漫", (
        "episode", "anime", "manga", "senpai", "chan", "kun", "opening", "ending",
        "火影", "海贼", "柯南", "番剧", "动漫", "动漫", "新番", "剧场版", "声优", "乙女",
        "轻小说", "初音", "jojo", "悟空")),
    ("game", "游戏电竞", (
        "gameplay", "player", "level", "boss", "quest", "rank", "hp", "mp", "loot",
        "steam", "playstation", "nintendo", "esports", "fps",
        "游戏", "玩家", "关卡", "boss", "装备", "排位", "开黑", "电竞", "主机", "手柄",
        "攻略", "通关", "皮肤", "buff", "血量")),
    ("movie", "影视剧集", (
        "movie", "film", "director", "actor", "actress", "scene", "plot", "trailer",
        "netflix", "series", "season",
        "电影", "电视剧", "导演", "演员", "剧情", "片段", "预告", "美剧", "剧集", "解说",
        "影评", "短片")),
    ("music", "音乐歌曲", (
        "lyrics", "chorus", "verse", "melody", "guitar", "piano", "album", "concert",
        "song", "beat",
        "歌词", "副歌", "旋律", "吉他", "钢琴", "专辑", "演唱会", "歌曲", "翻唱", "音乐",
        "伴奏", "谱")),
    ("news", "新闻时政", (
        "report", "press", "government", "president", "minister", "election", "policy",
        "breaking news", "correspondent",
        "报道", "记者", "政府", "总统", "选举", "政策", "新闻", "联播", "发言人", "局势",
        "国际", "外交")),
    ("sports", "体育运动", (
        "goal", "match", "tournament", "coach", "team", "league", "score", "champion",
        "olympic", "training",
        "进球", "比赛", "冠军", "联赛", "教练", "球队", "得分", "奥运", "训练", "健身",
        "篮球", "足球", "跑步")),
    ("food", "美食烹饪", (
        "recipe", "ingredient", "sauce", "bake", "oven", "tablespoon", "delicious",
        "spicy", "restaurant", "taste",
        "食谱", "食材", "调料", "烤箱", "一勺", "好吃", "辣", "餐厅", "味道", "美食",
        "探店", "做饭", "烹饪", "下厨")),
    ("travel", "旅行旅拍", (
        "travel", "hotel", "flight", "airport", "tourist", "sightseeing", "itinerary",
        "visa", "luggage",
        "旅行", "酒店", "航班", "机场", "景点", "打卡", "签证", "行李", "攻略", "自由行",
        "旅拍", "露营")),
    ("vlog", "生活日常", (
        "vlog", "daily", "routine", "haul", "unboxing", "weekend", "shopping",
        "friendo", "friend", "weather", "store", "shop",
        "日常", "生活", "开箱", "购物", "周末", "逛街", "朋友", "天气", "记录", "闲聊",
        "vlog", "一天")),
    ("comedy", "搞笑娱乐", (
        "funny", "joke", "laugh", "hilarious", "prank", "comedy", "standup",
        "搞笑", "段子", "笑话", "笑死", "整活", "吐槽", "综艺", "脱口秀", "沙雕")),
]

DEFAULT_DOMAIN = "default"
DOMAIN_LABEL = {d: label for d, label, _ in DOMAINS}
DOMAIN_LABEL[DEFAULT_DOMAIN] = "未分类"

# 关键词表编译成一次性的正则（单词边界匹配，避免 "ai" 命中 "said"）
_WORD_RE = re.compile(r"[a-z0-9]+")
_PHRASE_BONUS = 1.8      # 短语命中比单词命中更有区分度
_META_WEIGHT = 3.0       # 标题/UP主/分区里的关键词权重（比正文更可信）
_SATURATION = 4.0        # 到这个证据量就算"很确定"


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
    name = "base"

    def decide(self, text: str, meta: dict | None = None) -> DomainDecision:
        raise NotImplementedError

    def reset(self) -> None:
        """新的会话开始时调用。"""


class NoopRouter(RouterBackend):
    """完全关掉埋点：不判断、不记录。"""

    name = "none"

    def decide(self, text: str, meta: dict | None = None) -> DomainDecision:
        return DomainDecision(domain="unknown", stable="unknown", confidence=0.0,
                              evidence="", backend=self.name, cost_ms=0.0)


class HeuristicRouter(RouterBackend):
    """关键词打分 + 元数据加权 + 迟滞。纯 CPU 字符串匹配，微秒级。

    打分只用"命中的关键词个数 × 权重"，不用任何统计模型 —— 这样每个结论都能
    追溯到具体哪几个词命中了（`evidence`），事后复盘时不会变成黑箱。
    """

    name = "heuristic"

    def __init__(self, meta_weight: float = _META_WEIGHT, hysteresis: int = 2,
                 min_confidence: float = 0.50, history_chars: int = 4000,
                 extra_keywords: dict | None = None):
        self.meta_weight = float(meta_weight)
        self.hysteresis = max(1, int(hysteresis))
        self.min_confidence = float(min_confidence)
        self.history_chars = int(history_chars)
        self.keywords = self._compile(extra_keywords)
        self._history: list[str] = []
        self._pending: str | None = None
        self._pending_n = 0
        self._stable: str | None = None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _compile(extra_keywords: dict | None) -> dict[str, tuple[list[str], list[str]]]:
        """把关键词表编译成两组，并支持 `router.extra_keywords` 追加自定义词。

        为什么要分两组：**中文没有空格**，用"词边界集合"去匹配中文永远匹配不上
        （分词正则只认 `[a-z0-9]+`）。所以：
          · words = 纯 ASCII 单词 → 按词边界匹配（避免 "ai" 命中 "said"）
          · subs  = 含空格的多词短语 + 任何非 ASCII 词（中日韩）→ 按子串匹配
        """
        table: dict[str, dict[str, set]] = {}

        def add(domain: str, word: str) -> None:
            w = str(word).strip().lower()
            if not w:
                return
            bucket = table.setdefault(domain, {"words": set(), "subs": set()})
            if " " in w or not w.isascii():
                bucket["subs"].add(w)
            else:
                bucket["words"].add(w)

        for domain, _label, words in DOMAINS:
            for w in words:
                add(domain, w)
        for domain, words in (extra_keywords or {}).items():
            for w in words:
                add(domain, w)
        return {d: (sorted(v["words"]), sorted(v["subs"])) for d, v in table.items()}

    def reset(self) -> None:
        self._history.clear()
        self._pending = None
        self._pending_n = 0
        self._stable = None

    # ------------------------------------------------------------------ #
    def _accumulate(self, text: str) -> str:
        if text:
            self._history.append(text)
            total = sum(len(t) for t in self._history)
            while total > self.history_chars and len(self._history) > 1:
                total -= len(self._history.pop(0))
        return " ".join(self._history)

    @staticmethod
    def _score(words: list[str], subs: list[str], low: str, word_set: set,
               weight: float, hits: list[str], tag: str = "") -> float:
        s = 0.0
        for w in words:
            if w in word_set:
                s += weight
                hits.append(f"{tag}{w}")
        for sub in subs:
            if sub in low:
                s += weight * (_PHRASE_BONUS if " " in sub else 1.0)
                hits.append(f"{tag}{sub}")
        return s

    def decide(self, text: str, meta: dict | None = None) -> DomainDecision:
        import time as _t

        t0 = _t.perf_counter()
        body = self._accumulate(text)
        meta = meta or {}
        meta_text = " ".join(str(meta.get(k, "")) for k in ("title", "uploader", "tags",
                                                           "partition", "tname") if meta.get(k))

        # 小写与分词只做一次（15 个领域共用），别放进循环里重复算
        body_low = body.lower()
        body_words = set(_WORD_RE.findall(body_low))
        meta_low = meta_text.lower()
        meta_words = set(_WORD_RE.findall(meta_low)) if meta_low else set()

        raw: dict[str, float] = {}
        ev: dict[str, list[str]] = {}
        for domain, (words, subs) in self.keywords.items():
            hits: list[str] = []
            s = self._score(words, subs, body_low, body_words, 1.0, hits)
            if meta_low:
                s += self._score(words, subs, meta_low, meta_words, self.meta_weight, hits,
                                 tag="meta:")
            if s > 0:
                raw[domain] = s
                ev[domain] = hits

        if not raw:
            decision = DomainDecision(domain=DEFAULT_DOMAIN, confidence=0.0,
                                      evidence="", scores={}, backend=self.name)
        else:
            ranked = sorted(raw.items(), key=lambda kv: -kv[1])
            top_d, top_s = ranked[0]
            second_s = ranked[1][1] if len(ranked) > 1 else 0.0
            # 置信度 = 证据强度(75%) + 与第二名的差距(25%)，两项都能解释；
            # 单独命中的一个词只值 0.44（弱），两三个词命中才上 0.6+。
            strength = min(1.0, top_s / _SATURATION)
            margin = (top_s - second_s) / top_s if top_s else 0.0
            conf = round(0.75 * strength + 0.25 * margin, 3)
            domain = top_d if conf >= self.min_confidence else DEFAULT_DOMAIN
            decision = DomainDecision(
                domain=domain, confidence=conf,
                evidence=", ".join(list(dict.fromkeys(ev.get(top_d, [])))[:8]),
                scores={k: round(v, 2) for k, v in ranked[:4]}, backend=self.name)

        decision.stable = self._apply_hysteresis(decision.domain)
        decision.cost_ms = round((_t.perf_counter() - t0) * 1000.0, 3)
        return decision

    def _apply_hysteresis(self, guess: str) -> str:
        """连续 `hysteresis` 次同一个猜测才改判；否则维持上一次的稳定值。"""
        if self._stable is None:
            self._stable, self._pending, self._pending_n = guess, None, 0
            return self._stable
        if guess == self._stable:
            self._pending, self._pending_n = None, 0
            return self._stable
        if guess == self._pending:
            self._pending_n += 1
        else:
            self._pending, self._pending_n = guess, 1
        if self._pending_n >= self.hysteresis:
            log.info("领域判定切换: %s → %s", self._stable, guess)
            self._stable, self._pending, self._pending_n = guess, None, 0
        return self._stable


def make_router(cfg) -> RouterBackend:
    backend = str(cfg.get_path("router.backend", "heuristic")).lower()
    if backend in ("none", "off", "disabled"):
        return NoopRouter()
    if backend != "heuristic":
        log.warning("未知的 router.backend=%s，回退到 heuristic", backend)
    return HeuristicRouter(
        meta_weight=float(cfg.get_path("router.meta_weight", _META_WEIGHT)),
        hysteresis=int(cfg.get_path("router.hysteresis", 2)),
        # 兜底值必须和 config.yaml 里的一致，否则「配置说 0.50、实际跑 0.30」
        min_confidence=float(cfg.get_path("router.min_confidence", 0.50)),
        history_chars=int(cfg.get_path("router.history_chars", 4000)),
        extra_keywords=dict(cfg.get_path("router.extra_keywords", {}) or {}),
    )
