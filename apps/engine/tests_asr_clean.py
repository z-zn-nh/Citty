"""clean_sense_voice_text 的单测 —— 用例全部取自 62 分钟长测的真实输出。

    python apps\\engine\\tests_asr_clean.py      （或 pytest 直接收）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from citty.asr import clean_sense_voice_text as clean  # noqa: E402

CASES = [
    # 长测里最常见的形态：单词后面直接跟一个半截标签的 `>`
    ("They said we'd hear back by this weekma So I'm trying to be patient>.",
     "They said we'd hear back by this weekma So I'm trying to be patient."),
    ("I keep refreshing my email like a crazy personperio>.",
     "I keep refreshing my email like a crazy personperio."),
    ("I genuinely cannot tell the difference, but, okay,>.",
     "I genuinely cannot tell the difference, but, okay,."),
    ("I know every time my phone buzzes, I think it's the<>.",
     "I know every time my phone buzzes, I think it's the."),
    ("她说好得过头了 >", "她说好得过头了"),
    # 逗号重复 + 标点前多空格
    ("They said we'd hear back by this week,, So I'm trying to be patient>.",
     "They said we'd hear back by this week, So I'm trying to be patient."),
    ("we'd hear back by this week , So I'm trying to be patient",
     "we'd hear back by this week, So I'm trying to be patient"),
    # 完整标签（正常情况）
    ("<|zh|><|NEUTRAL|><|Speech|><|withitn|>谢谢你。", "谢谢你。"),
    ("<|en|><|HAPPY|><|Speech|><|woitn|>Hello there.", "Hello there."),
    # 必须不能误伤
    ("他没说话，只是点了点头。", "他没说话，只是点了点头。"),
    ("$50 dollarsma max.", "$50 dollarsma max."),
    ("Wait... I don't know.", "Wait... I don't know."),     # 省略号不能被压成句号
    ("A drama about trauma and cinema.", "A drama about trauma and cinema."),
    ("", ""),
]


def test_clean_sense_voice_text() -> None:
    bad = [(src, clean(src), want) for src, want in CASES if clean(src) != want]
    assert not bad, "\n".join(f"  {s!r}\n   得到 {g!r}\n   期望 {w!r}" for s, g, w in bad)


if __name__ == "__main__":
    fails = [(s, clean(s), w) for s, w in CASES if clean(s) != w]
    for s, w in CASES:
        got = clean(s)
        print(f"  {'✅' if got == w else '❌'} {s[:52]!r}")
        if got != w:
            print(f"       得到 {got!r}\n       期望 {w!r}")
    print(f"\n  {len(CASES) - len(fails)}/{len(CASES)} 通过")
    raise SystemExit(1 if fails else 0)
