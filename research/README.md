# 资料与来源索引

> 采集日期：**2026-10-06**。全部为当日直接从 Hugging Face API / raw.githubusercontent / GitHub API 拉取的一手内容，未经二次转述。
> 注意：本会话内置的 `web_search` 工具因 API Key 无效（HTTP 401）不可用，因此改用 HTTP 直取官方仓库/模型卡，`raw/` 下为原文快照。

## raw/ 文件对照

| 文件 | 来源 | 用途 |
|---|---|---|
| `index-translate-readme.md` | <https://raw.githubusercontent.com/bilibili/Index-Translate/main/README_zh.md> | B 站翻译模型家族总览、免费 API、评测表、默认推理参数 |
| `index-translate-readme-en.md` | 同上（英文版） | 交叉核对 |
| `index-echo-s2tt-usage.md` | `.../inference/echo-s2tt/README_zh.md` | S2TT 字幕用法（`--max-win/--ctx-k/--glossary`）、显存与实时率 |
| `index-echo-s2tt-9b.md` / `-full.md` | <https://huggingface.co/IndexTeam/Index-Echo-S2TT-9B> | S2TT 模型卡 |
| `index-echo-s2st-9b.md` | <https://huggingface.co/IndexTeam/Index-Echo-S2ST-9B> | S2ST 配音模型卡：六方向、24kHz、Hidden2CV+CosyVoice3、`chunk=False` |
| `index-video-dub.md` | `.../video-dub/README_zh.md` | 官方配音管线（demucs 分离、VAD 切句、时间轴对齐、22GB 显存） |
| `index-extension.md` | `.../extension/README_zh.md` | 官方 MV3 网页翻译扩展（前端骨架参考、已知局限） |
| `indextts-2.5.md` | <https://huggingface.co/IndexTeam/IndexTTS-2.5> | B 站 TTS：0.8B、~6GB 显存、5 语种、情绪/语速/发音控制、许可 |
| `hunyuan-mt-7b.md` | <https://huggingface.co/tencent/Hunyuan-MT-7B> | 腾讯上一代 MT（2025-09，33 语种，WMT25 30/31 第一，Chimera 集成） |
| `hy-mt2-7b.md` / `hy-mt2-30b.md` | <https://huggingface.co/tencent/Hy-MT2-7B> · `-30B-A3B` | 腾讯 **Hy-MT2**（2026-05-21）：1.8B/7B/30B-A3B、33 语种、Apache-2.0、1.25bit 量化、domain-specific 评测、WMT26 视频字幕任务 |
| `jev-9b.md` | <https://huggingface.co/autotrust/JEV-9B> | JEV-9B：System1/System2、类型化决策、校准概率、163ms/决策、视觉版、`/v1/decide` |
| `jev-27b-vl.md` | <https://huggingface.co/autotrust/JEV-27B-VL> | JEV-27B-VL：2~256 选项、256K prompt、零样本短视频推荐 AUC 0.727、239ms/决策 |
| `nemotron-asr-streaming.md` | <https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b> | 流式 ASR：0.6B、chunk 80/160/320/560/1120ms、36 locale、FLEURS WER、OpenMDW-1.1 |
| `vibevoice-asr-streaming.md` | <https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B> | 流式说话人归属 ASR：MIT、10 语种、热词 |
| `whisperlivekit.md` | <https://raw.githubusercontent.com/QuentinFuxa/WhisperLiveKit/main/README.md> | 流式 ASR 工程框架：SimulStreaming/AlignAtt、NLLB 200 语种同传、AlignAtt4LLM、Sortformer、后端矩阵、CLI/WS 接口 |

## 关键外部链接

**模型与仓库**
- bilibili/Index-Translate：<https://github.com/bilibili/Index-Translate> · Demo <https://index-translate.bilibili.com/> · 免费 API `https://index-translate.bilibili.com/v1` · 技术报告 <https://arxiv.org/abs/2609.40181>
- IndexTeam 组织（HF）：<https://huggingface.co/IndexTeam>
- tencent/Hy-MT2：<https://github.com/Tencent-Hunyuan/Hy-MT2> · 报告 <https://arxiv.org/pdf/2605.22064>
- autotrust（JEV 家族）：<https://huggingface.co/autotrust>
- WhisperLiveKit：<https://github.com/QuentinFuxa/WhisperLiveKit>
- FunASR：<https://github.com/modelscope/FunASR> · Fun-ASR：<https://github.com/QwenAudio/Fun-ASR>
- kyutai-labs/hibiki（流式语音翻译参考）：<https://github.com/kyutai-labs/hibiki>

**参考竞品（都是离线/半离线，实时是空白）**
| 项目 | ★ | 定位 |
|---|---:|---|
| <https://github.com/Huanshere/VideoLingo> | 18.7k | 一键全自动字幕组（切分/翻译/对齐/配音） |
| <https://github.com/QuentinFuxa/WhisperLiveKit> | 11.1k | 超低延迟自托管转写 + 同传 |
| <https://github.com/liuzhao1225/YouDub-webui> | 5.6k | YouTube/B 站视频本地化配音 |
| <https://github.com/buxuku/SmartSub> | 5.6k | 桌面端字幕/翻译/配音/烧录 |
| <https://github.com/kyutai-labs/hibiki> | 1.5k | 流式语音翻译模型 |

## 待补（当前未核实，写文档时已标"待验证"）

- Index-Translate 2B/9B 在 **RTX 4060 Laptop 8GB** 上的真实首 token/整句延迟（需实测）。
- 哔哩哔哩免费 API 的**限流与 SLA**（官方未公开）。
- **Hy-MT2 在科技类语料上是否优于 Index-Translate**（无公开分领域对比，需自建 A/B）。
- JEV-9B/Chat 在 8GB 卡上的 **GGUF + 决策适配器**可用性（社区有 GGUF，但 System1 adapter 走 vLLM 的路径未验证）。
- IndexTTS-2.5 的 bilibili 自定义许可**商用条款细节**。
- TranslateGemma 的完整模型卡（HF 返回 401，未取到）。
