"""HeuristicRouter 的单测 —— 全部用例都来自真实踩过的坑。

    python apps\\engine\\tests_router.py      （或 pytest 直接收）

为什么要锁这些用例：P3-b 一定会来动阈值和词表，而"改完之后哪些视频还判得对"
不该靠重新跑一遍长视频才知道。这里把**标定边界**固定住：
单命中必须记 default、两命中才算数、中文必须能匹配上、元数据必须加权、迟滞必须生效。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from citty.router import DEFAULT_DOMAIN, HeuristicRouter, NoopRouter  # noqa: E402

# (说明, 正文, 元数据, 期望领域)
CASES = [
    ("英文科技", "We tested the new GPU and compared the CPU benchmarks.", {}, "tech"),
    ("英文游戏", "New gameplay! I beat the final boss and got better loot.", {}, "game"),
    ("英文美食", "Add two tablespoons of sauce, then bake it in the oven.", {}, "food"),
    ("英文医学", "The patient was given treatment for the infection.", {}, "medical"),
    ("英文番剧", "In this episode the anime protagonist fights his rival.", {}, "anime"),
    # 中文必须能匹配上（分词正则只认 [a-z0-9]+，中文走子串匹配）
    ("中文影视", "这部电影的导演和演员都很厉害，剧情也很紧凑。", {}, "movie"),
    ("中文美食", "把食材放进烤箱，再加一勺调料，味道很好。", {}, "food"),
    ("中文旅行", "这次自由行的酒店和航班都订好了，行李也收拾完了。", {}, "travel"),
    # 单命中不认领域（阈值 0.50；这是实测踩过的坑）
    ("单命中=friend", "She said hello and smiled at her friend.", {}, DEFAULT_DOMAIN),
    ("歧义词 patient（耐心的，不是病人）", "We wait, so I am trying to be patient.", {}, DEFAULT_DOMAIN),
    # 没有信号
    ("口语填充词", "uh huh okay yeah sure", {}, DEFAULT_DOMAIN),
    ("空文本", "", {}, DEFAULT_DOMAIN),
    # 元数据加权：正文一样，靠标题定领域
    ("靠标题判语言学习", "She said hello and smiled.", {"title": "英语听力练习 跟读口语"}, "language"),
    # 元数据命中是 ×3.0 权重，所以单个分区词也够（这是刻意的：标题/分区比正文可信）
    ("靠分区判科技（单个词也够）", "She said hello and smiled.", {"partition": "数码"}, "tech"),
    ("靠标题判游戏", "I love this so much.", {"title": "游戏实机演示 通关攻略"}, "game"),
    # 中文标题也要能命中
    ("中文标题判语言", "The teacher said it again.", {"title": "英语听力 口语练习"}, "language"),
]

# 累积效应：单看某一句不够，多句累积必然收敛到正确领域
ACCUM = [
    ("doctor patient diagnosis infection", "medical"),
    ("the hospital and the vaccine", "medical"),
    ("treatment dose clinical trial", "medical"),
]


def test_heuristic_router() -> None:
    bad = []
    for label, text, meta, want in CASES:
        r = HeuristicRouter()
        got = r.decide(text, meta).domain
        if got != want:
            bad.append(f"  {label}: 得到 {got} 期望 {want}")
    assert not bad, "\n".join(bad)


def test_router_history_reaches_threshold() -> None:
    """单句证据不足时，多句累积应该自己收敛（这正是"先埋点再路由"的前提）。"""
    r = HeuristicRouter()
    for text, _ in ACCUM:
        d = r.decide(text)
    assert d.domain == "medical", f"累积 3 句后应判 medical，实得 {d.domain}"


def test_router_hysteresis() -> None:
    """连续 N 次同一猜测才改判，避免领域来回跳。"""
    r = HeuristicRouter(hysteresis=2)
    r.decide("doctor patient diagnosis")                       # 建立 medical
    assert r._stable == "medical"
    assert r.decide("recipe oven ingredient").stable == "medical"   # 第 1 次 → 不改
    assert r.decide("recipe sauce bake").stable == "food"           # 第 2 次 → 改判


def test_router_none_is_silent() -> None:
    d = NoopRouter().decide("anything at all")
    assert d.domain == "unknown" and d.cost_ms == 0.0 and d.stable == "unknown"


if __name__ == "__main__":
    fails = 0
    print("== 领域判定 ==")
    for label, text, meta, want in CASES:
        r = HeuristicRouter()
        d = r.decide(text, meta)
        ok = d.domain == want
        fails += not ok
        print(f"  {'✅' if ok else '❌'} {label:34} → {d.domain:8} conf={d.confidence:.3f}"
              f"  ev=[{d.evidence[:38]}]")
        if not ok:
            print(f"       期望 {want}")

    print("\n== 累积收敛 ==")
    r = HeuristicRouter()
    for text, want in ACCUM:
        d = r.decide(text)
        print(f"  {'✅' if d.stable == want else '❌'} {text[:44]:46} → stable={d.stable}")
    fails += d.stable != ACCUM[-1][1]

    print("\n== 迟滞 / 关闭态 ==")
    r = HeuristicRouter(hysteresis=2)
    r.decide("doctor patient diagnosis")
    s1 = r.decide("recipe oven ingredient").stable
    s2 = r.decide("recipe sauce bake").stable
    ok = s1 == "medical" and s2 == "food"
    fails += not ok
    print(f"  {'✅' if ok else '❌'} 第1次改判保持={s1}  第2次改判生效={s2}")
    n = NoopRouter().decide("x")
    ok2 = n.domain == "unknown" and n.cost_ms == 0.0
    fails += not ok2
    print(f"  {'✅' if ok2 else '❌'} NoopRouter 静默: {n.to_dict()}")

    print(f"\n  总计 {'全部通过' if not fails else f'{fails} 项失败'}")
    raise SystemExit(1 if fails else 0)
