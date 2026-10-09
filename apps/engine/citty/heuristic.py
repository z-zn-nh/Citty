"""启发式领域判断（P3-a 唯一的后端）：关键词打分 + 元数据加权 + 迟滞。

零模型、零显存、纯 CPU 字符串匹配，实测**单次判断 0.1ms 量级**（长测里 `router_ms` 会记录）。

打分规则（全部可解释，没有黑箱，每个结论都能追溯到命中了哪几个词）：

| 来源 | 分值 |
|---|---|
| 正文命中一个 ASCII 单词（按词边界，避免 "ai" 命中 "said"） | 1.0 |
| 正文命中一个多词短语 | ×1.8 |
| 正文命中一个中文词（按子串，中文没空格） | 1.0 |
| 命中出现在标题/UP主/分区等元数据里 | ×3.0 |

置信度 = `0.75 × 证据强度 + 0.25 × 领先第二名的差距`，证据强度 = `min(1, 得分 / 4)`：

    单个词 0.44 ｜ 两个词 0.62 ｜ 三个词 0.81 ｜ 一个多词短语 0.59

**阈值 0.50（`min_confidence`）的标定依据**：单词级匹配有同形异义问题，实测撞到过
"…try to be patient" 里的 `patient` 命中医学词表，单独一个词就把整段判成"医学"。
所以定成"至少两个词命中才认领域，单命中记 `default`"，宁缺勿滥；证据仍然照记
（`DomainDecision.scores` 里保留原始打分），事后能从 `default` 里捞回来重标。

**元数据比正文值钱**（实测对比同一段音频）：只喂正文时首段置信度 0.438（判不出）；
喂了标题「保命级英文听力训练｜闲聊口语」后首段就是 `language`，置信度 0.875。
"""
from __future__ import annotations

import logging
import re

from .router import DEFAULT_DOMAIN, DomainDecision, RouterBackend

log = logging.getLogger("citty.router")

# --------------------------------------------------------------------------- #
# 领域表：15 个 + default（P3-b 的 JEV 选择项要求 8~16 个这个量级）。
# 关键词中英双语都收 —— 源语言可能是中文也可能是英文。
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
        "症状", "诊断", "患者", "治疗", "剂量", "临床", "手术", "血压", "感染",
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
        "sentence", "repeat after me",
        "听力", "口语", "语法", "单词", "词汇", "发音", "英语", "例句", "跟读", "背单词",
        "口语练习", "保母级", "保姆级")),
    ("anime", "番剧动漫", (
        "episode", "anime", "manga", "senpai", "chan", "kun", "opening", "ending",
        "火影", "海贼", "柯南", "番剧", "动漫", "新番", "剧场版", "声优", "乙女",
        "轻小说", "初音", "jojo", "悟空")),
    ("game", "游戏电竞", (
        "gameplay", "player", "level", "boss", "quest", "rank", "loot",
        "steam", "playstation", "nintendo", "esports",
        "游戏", "玩家", "关卡", "装备", "排位", "开黑", "电竞", "主机", "手柄",
        "攻略", "通关", "皮肤", "血量")),
    ("movie", "影视剧集", (
        "movie", "film", "director", "actor", "actress", "scene", "plot", "trailer",
        "netflix", "series", "season",
        "电影", "电视剧", "导演", "演员", "剧情", "片段", "预告", "美剧", "剧集", "解说",
        "影评", "短片")),
    ("music", "音乐歌曲", (
        "lyrics", "chorus", "verse", "melody", "guitar", "piano", "album", "concert",
        "song", "beat",
        "歌词", "副歌", "旋律", "吉他", "钢琴", "专辑", "演唱会", "歌曲", "翻唱", "音乐",
        "伴奏")),
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
        "friend", "weather", "store", "shop",
        "日常", "生活", "开箱", "购物", "周末", "逛街", "朋友", "天气", "记录", "闲聊",
        "一天")),
    ("comedy", "搞笑娱乐", (
        "funny", "joke", "laugh", "hilarious", "prank", "comedy", "standup",
        "搞笑", "段子", "笑话", "笑死", "整活", "吐槽", "综艺", "脱口秀", "沙雕")),
]

DOMAIN_LABEL = {d: label for d, label, _ in DOMAINS}
DOMAIN_LABEL[DEFAULT_DOMAIN] = "未分类"

_WORD_RE = re.compile(r"[a-z0-9]+")
_PHRASE_BONUS = 1.8      # 多词短语命中比单词命中更有区分度
_META_WEIGHT = 3.0       # 标题/UP主/分区里的关键词权重（比正文更可信）
_SATURATION = 4.0        # 到这个证据量就算"很确定"
_META_KEYS = ("title", "uploader", "tags", "partition", "tname")


class HeuristicRouter(RouterBackend):
    """关键词打分 + 元数据加权 + 迟滞。有状态：会累积正文、维护稳定判定。"""

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
        """累积正文（上限 history_chars，超出丢最旧的）——多听几句判得准。"""
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
        meta_text = " ".join(str(meta.get(k, "")) for k in _META_KEYS if meta.get(k))

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
        """连续 `hysteresis` 次同一个猜测才改判；否则维持上一次的稳定值。

        P3-a 里它只影响**记录**哪一次算"稳定判定"，不影响翻译；P3-b 才让它真正参与路由。
        """
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
