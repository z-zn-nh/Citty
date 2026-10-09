"""Citty 实时视频翻译引擎（云端 API + 本地 CPU 双档）。

设计原则：
- 本地档不下载模型权重，只装 onnxruntime（sherpa-onnx）；云端档全走 HTTP API；
- 音频捕获 → VAD 分句 → ASR → MT → 修正 pass → 字幕广播，全链路可观测；
- 后端可插拔：asr.backend / mt.backend 切换实现。
"""

import sys


def tame_console_encoding() -> None:
    """让 stdout/stderr 遇到"编不出来"的字符时退化，而不是把整个进程搞崩。

    真实踩到过：Windows 中文控制台默认 cp936(GBK)，而我们的输出里有 `▸` `✅` `❌`
    这类字符，GBK 里**没有码位**。Python 默认 strict 模式会抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\u25b8'`，
    于是"打印一行状态"变成了整条流水线 `PIPELINE_FAILED`（一次 6 段全废）。

    注意**不要**强制改成 utf-8：往 GBK 控制台写 UTF-8 会让中文全变乱码。
    保留控制台自己的编码，只把 errors 放宽成 replace，最多掉一个方框，不会崩。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # 不是 TextIOWrapper（被重定向/包装过）就算了
            pass


tame_console_encoding()

__version__ = "0.1.0"
