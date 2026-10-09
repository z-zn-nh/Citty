# Citty · 实时视频翻译插件 —— 调研与可行性文档

针对"实时翻译视频 + TTS 接管原声 + JEV 路由分模型 + 多语种"四阶段设想的可行性调研与设计文档。
调研基线 **2026-10-06**，目标机器 **Windows / i7-12850HX / 32GB / RTX 4060 Laptop 8GB**。

---

## TL;DR

| 阶段 | 可行性 | 一句话 |
|---|---|---|
| P1 实时字幕（中↔英 + 双语） | **高**（MVP 2~3 周） | 走"流式 ASR + 文本 MT + 修正 pass"，端到端可压到 0.7~1.5s |
| P2 TTS 接管原声 | 离线**高**／实时**中** | 端到端 S2ST 单卡要 ~22GB，本机只能用 ASR+MT+IndexTTS-2.5 串联；实时只能做到"延迟 3~8s 的影子配音" |
| P3 JEV 判断视频类型 → 分模型 | **可行但需 A/B 验证** | JEV 是**决策模型**（一次前向、带校准概率，163ms/次），天生适合路由；但 9B/27B 与 8GB 显存冲突 |
| P4 多语种 | **无阻塞** | 翻译侧已有 150 语种；瓶颈在 TTS 语种、字幕排版与评测 |

**三条硬约束**：① 8GB 显存装不下 ASR+MT+TTS+路由，必须分档 + 云端兜底；② 实时配音的物理下限是 2~5s/句；③ AI 配音有强制标识与声音权的合规要求。

**执行顺序（2026 调整后）**：**P1 ✅ → P3-a 埋点 → P4 多语种（先只做字幕）→ P3-b 路由生效 → P2-a 离线配音 → P2-b 实时配音**。
配音整体推后（TTS 选型由用户自行决定）；前置项是**第二个真实 MT 模型**（单模型无从路由）。理由、代价与依赖核查见 [04-路线图与风险](docs/04-路线图与风险.md)。

---

## 文档

| 文档 | 内容 |
|---|---|
| [01-可行性报告](docs/01-可行性报告.md) | 主报告：结论、模型现状（B 站 Index-Translate / 腾讯 Hy-MT2 / JEV 到底是什么）、链路可行性、硬件约束、工期、风险、明确建议 |
| [02-模型与工具选型](docs/02-模型与工具选型.md) | 选型矩阵、三款 MT 横向对比、ASR/TTS 候选、JEV 接法、"科技类更适合腾讯"的验证方案（A/B 评测集）、许可证速查 |
| [03-架构设计](docs/03-架构设计.md) | 产品形态取舍、进程/模块图、Segment 数据结构与 WebSocket 协议、延迟预算、显存账本、字幕三模式、PoC 骨架代码、观测埋点 |
| [04-路线图与风险](docs/04-路线图与风险.md) | 四阶段里程碑与验收标准、风险登记表（含触发条件与响应）、合规清单、**下一步 7 天计划** |
| [05-引擎使用说明](docs/05-引擎使用说明.md) | **已跑通的引擎**：三分钟上手、实测延迟、接入真实 ASR 的四家配置、WebSocket 协议、排障、下一步 |
| [06-ASR选型](docs/06-ASR选型.md) | **SenseVoice / Fun-ASR-Nano / Whisper-large-v3-turbo / Qwen3-ASR 四家对比**：硬指标、9 条判据打分、分档方案、显存账本、`citty ab` 自测方法 |
| [07-本地部署可行性](docs/07-本地部署可行性.md) | **这台电脑能不能本地跑**：机器实测体检、各模型精确下载量（HF 元数据实测）、8GB 显存三个分档方案、五个坑、分步执行顺序 |
| [08-远端服务器部署方案](docs/08-远端服务器部署方案.md) | **租一台云服务器跑推理**：三种架构、实测网络 RTT 与带宽账本、服务器/平台/成本对比、SSH 隧道安全方案、六步落地路径、远端特有的坑 |
| [09-VPS规格汇总](docs/09-VPS规格汇总.md) | **四阶段全实现要买多大的机器**：推荐 24GB 显存/16vCPU/64GB/200GB；组件级显存与磁盘账本、四档配置对照、为什么不是 8/16/48GB、买机六个注意点 |
| [10-免费方案与最小部署](docs/10-免费方案与最小部署.md) | **最少部署原则**：免费资源清单（哪些连 key 都不用）、每阶段 VPS 需求（结论：**0**）、免费方案的真实代价、edge-tts 实测数据 |
| [11-Kaggle与AIStudio评估](docs/11-Kaggle与AIStudio评估.md) | **免费 GPU 实验台选哪个**：Kaggle（T4×2=32GB）vs 飞桨 AI Studio（V100、国内顺）；T4/V100 无 bf16 对我们模型的影响、为什么都不能当实时服务、怎么用才最值 |
| [12-可本地部署模型清单](docs/12-可本地部署模型清单.md) | **各阶段能本地跑的模型全清单**（体积全部实测）：ASR 10 款、MT 8 款、TTS 7 款、路由 3 款；六个 runner 对照；零显存 CPU 档只要 1.9GB |
| [13-P1本地部署最简方案](docs/13-P1本地部署最简方案.md) | **已跑通**：P1 本地化只要 **1 个 228MB 文件 + `pip install sherpa-onnx`**，CPU 上 RTF **0.021**、中文 CER **0.0**；13.5 秒是延迟悬崖；踩到的 3 个坑 |
| [14-P1验收与测试](docs/14-P1验收与测试.md) | **P1 已验收**：完工定义逐条对照、`.\test-p1.ps1` 四步自测法（含实时链路）、排障表、还没覆盖的边界 |
| [15-一小时长测记录](docs/15-一小时长测记录.md) | **62 分钟真实视频连续跑**：992 段零崩溃、内存不涨、免费接口 994 次无限流；长测挖出的标签碎片 bug（9.1% → 0%）与根因定位全过程 |

**远端部署脚手架**（`deploy/`，已写好，租到机器就能用）：

| 文件 | 作用 |
|---|---|
| `deploy/server/bootstrap.sh` | 服务器一键部署：编译 llama.cpp、下 GGUF 权重、装 FunASR、起两个 systemd 服务（只监听 127.0.0.1） |
| `deploy/server/asr_openai_server.py` | 把 FunASR/SenseVoice 包成 OpenAI 兼容的 `/v1/audio/transcriptions`（本地引擎零改动） |
| `deploy/kaggle/asr_ab_local.py` | **免费 GPU 上的本地 ASR 选型 A/B**：SenseVoice / Whisper-turbo / Fun-ASR-Nano 并排测延迟、RTF、CER/WER（已用 `--dry-run` 本机验证） |
| `deploy/tunnel.ps1` | 本地建 SSH 隧道 + 连通性自检（`-Check` / `-Stop`），不需要开放任何公网端口 |
| `deploy/probe_remote.py` | **远端机器体检**（零依赖，新机器直接跑）：CPU/内存/磁盘/GPU/显存/**CUDA 是否真可用**/下载源连通性/CPU 基准，末尾直接给"这台能跑哪些 P2 组件"的结论 |
| `config.remote.yaml` | 指向隧道的配置档，用法：`.\run.ps1 -Config D:\Citty\config.remote.yaml file out\samples\official_en.wav 30` |
| `config.local.yaml` | **全本地档**（ASR 走 sherpa-onnx CPU，翻译走免费接口），用法：`.\run.ps1 -Config D:\Citty\config.local.yaml file out\samples\official_en.wav 18` |
| `test-p1.ps1` | **P1 一键验收**（四步：doctor / file 全链路 / 声卡探针 / 实时链路），用法：`.\test-p1.ps1` |
| [research/README](research/README.md) | 资料索引：所有一手来源链接与 `research/raw/` 原文快照对照表 |

**自测与运维小工具**（`tools/`，全部零依赖、零窗口）：

| 工具 | 作用 |
|---|---|
| `tools/probe_loopback.py` | 验证"抓系统声音"能不能用；`--sweep` 逐个设备配对扫，找出**能抓的那个** |
| `tools/play_wav.py` | 把 WAV 播到**指定输出设备**，支持 `--start/--seconds` 精确定位到某段（复现问题用） |
| `tools/fetch_audio.py` | 抓视频音频 → 16k 单声道 WAV；`--search` 直接搜 B 站并自动挑最长的一条 |
| `tools/check_audio.py` | 长测前素材体检：时长、每秒音量分布、静音占比、抽查转写 |
| `tools/monitor_longrun.py` | 长测后台监控：采段数/内存/CPU/离线状态写 CSV，收尾自动打印总结 |
| `tools/analyze_srt.py` | 字幕质量体检：标签残留、碎句率、重复句、逐条抽样 |
| `tools/probe_asr_key.py` / `probe_ws.py` / `mock_asr_server.py` | ASR key 探测 / WebSocket 协议检查 / 替身 ASR 服务 |

---

## 引擎已跑通（2026-10-06 实测）

两条路都通了：**全云端**（ASR 走云端 API，翻译用哔哩哔哩免费接口无需 key）、
**全本地 ASR**（`sherpa-onnx` + SenseVoice int8，CPU 跑，228MB，零显存零 key）。
TTS 用 edge-tts（免费、无 key、无权重）。

```powershell
powershell -File D:\Citty\setup.ps1   # 建环境（约 65MB，只装音频捕获/服务端依赖）
.\run.ps1 doctor                       # 自检
.\run.ps1 selftest 30                  # 无界面跑通全链路
.\demo.ps1                             # 引擎 + 桌面置顶字幕窗
.\run.ps1 voices zh-CN                 # 列出免费 TTS 语音（中文 8 个，含辽宁/陕西话）
.\run.ps1 say "要配音的文本" -Out out\x.mp3   # 免费配音（P2 影子配音）
```

**本地 ASR 档**（不需要任何 key）：
```powershell
D:\Citty\.venv\Scripts\python.exe -m pip install sherpa-onnx    # 28MB，不是 torch
.\test-p1.ps1                                                   # 一键四步验收（P1 已 PASS）
.\run.ps1 -Config D:\Citty\config.local.yaml doctor
.\run.ps1 -Config D:\Citty\config.local.yaml file out\samples\official_en.wav 18
```

| 实测项 | 结果 |
|---|---|
| **P1 端到端延迟** | ✅ **487ms**（本地 ASR 86ms + 免费翻译 401ms），实时截图见 [14](docs/14-P1验收与测试.md) |
| 真实 ASR 全链路 | **真实音频（24bit/48k 英文）→ 8 段语音 → 7 句翻译**，0 丢弃 0 报错，SRT 时间轴对齐 |
| 真实 ASR 延迟 | SenseVoice **RTF 0.161** / Qwen3-ASR-1.7B **0.173** / XingChenASR 0.304（15.1s 音频，含上传） |
| 端到端单句延迟 | 约 **1.8~3.1s**（ASR 含上传 ≈0.9s + 翻译 0.8~2.1s） |
| 翻译双向 | ✅ en→zh 801ms、✅ zh→en 574ms（哔哩哔哩免费 API，无需 key） |
| **全本地 ASR（P1，无 key）** | ✅ sherpa-onnx SenseVoice int8 **CPU**：5 秒片段 **110ms（RTF 0.022）**、中文样例 **CER 0.0**、5 段 5 译 0 错 |
| **免费 TTS（P2）** | ✅ edge-tts：中文 29 字 → 6.76s 音频 / **1177ms（RTF 0.17）**；322 个语音、100+ 语言 |
| 远端配置（SSH 隧道那套） | ✅ 用替身服务实测：6 段 / 6 句翻译 / 0 报错 |
| 系统声音捕获 | ✅ WASAPI loopback 实拍验证，VAD 正确切出 2/2 段 |
| **62 分钟长测（连续跑）** | ✅ **992 段 / 992 译 / 0 丢弃 / 1 次瞬时错误**，内存 408→331MB（无泄漏）、CPU 均值 4%、免费接口 994 次请求无限流 |
| 前端协议 | ✅ WebSocket 事件流（partial/stable/final + rev 修订）字段齐全 |
| 磁盘 | 项目本体 **7MB**；唯一下载的模型是本地 ASR 的 **228MB** |

**真实数据暴露并修掉的 bug**：24-bit WAV 崩溃、`file` 模式不按真实时间播放（45 秒刷出 397 段）、
ASR 失败无限重试（见 [05](docs/05-引擎使用说明.md)）；`NO_PROXY` 里的 `[::1]` 让 httpx 在构造阶段就崩
（新增 `citty/net.py` 兜底）、参考文本 BOM 让 CER/WER 算错（改用 `utf-8-sig`）、
中文控制台下 `▸` 触发 `UnicodeEncodeError` 把整条流水线搞崩（`citty/__init__.py` 放宽编码）、
切句超过 13.5 秒 CPU 推理成本跳 5 倍（见 [13](docs/13-P1本地部署最简方案.md)）；
**长测挖出的 9.1% 字幕标签碎片**（`patient>` / `weekma`）与 loopback 采集丢块的关系（见 [15](docs/15-一小时长测记录.md)）。

**选哪个 ASR？** 结论是 **Qwen3-ASR 主用、Whisper-turbo 只跑英文、SenseVoice 走 CPU 当零显存兜底**，
并且已经内置了自助 A/B 工具让你用**自己的视频**验证：

```powershell
.\run.ps1 record 40           # 播着目标视频，录 40 秒系统声音
.\run.ps1 ab out\sample.wav   # 多家云端 ASR 并排跑同一段音频（延迟 / RTF / 转写）
.\run.ps1 ab out\sample.wav out\ref.txt   # 手打标准答案 → 自动算 CER/WER
```

详见 [06-ASR选型](docs/06-ASR选型.md) 与 [05-引擎使用说明](docs/05-引擎使用说明.md)。

---

## 先看这三个结论

1. **P1 已完成，它是后面所有阶段的地基**：音频捕获（WASAPI loopback）、字幕渲染、时间轴同步、修正 pass 都已实测跑通（62 分钟长测 992 段零丢弃）。
2. **别一上来做实时配音和模型路由**：实时配音延迟下限 2~5s/句，投入产出比最差；路由在没有评测集之前等于随机数。这也是**配音被推到最后的理由之一**。
3. **接口先留对**：`ASRBackend / MTBackend / TTSBackend / RouterBackend` 四个 adapter + 统一 `Segment` 结构，P2/P3/P4 才能是加法而不是重写。**TTS 只冻结接口不冻结模型** —— 选型由用户决定。

## 立刻能做的第一步

见 [04-路线图与风险](docs/04-路线图与风险.md#7-下一步-7-天可以立刻执行)，以及 [03-架构设计](docs/03-架构设计.md#7-poc-骨架第一周就能跑通的最小链路) 里的 PoC 代码：
`WASAPI loopback 捕获 → VAD/攒批 → Whisper → Index-Translate-2B 翻译 → 透明置顶字幕窗`。

## 环境现状（本机探测）

| 项 | 状态 |
|---|---|
| GPU | RTX 4060 Laptop **8GB**（驱动 572.83） |
| CPU / RAM | i7-12850HX / 24 线程 / 31.7GB |
| 已有 | Python、uv、node、pnpm、git |
| **缺** | **ffmpeg（需装）**、ollama、docker |
| 网络 | 可访问 Hugging Face / GitHub |
