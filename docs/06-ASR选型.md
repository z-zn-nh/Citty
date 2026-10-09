# ASR 选型：SenseVoice / Fun-ASR-Nano / Whisper-large-v3-turbo / Qwen3-ASR

> 调研基线 2026-10-06，一手来源见每节引用与 [research/README](../research/README.md)。
> 结论先行，后面给依据和验证方法——**最终请用你自己视频的样本决定，不要信任何人的榜单**。

---

## 0. 结论

**主力选 Qwen3-ASR**（云端用 `qwen3-asr-flash`；将来本地化用 `0.6B` + vLLM 流式）；
**Whisper large-v3-turbo 只当"纯英文视频"的快速档**；
**SenseVoice-Small 是我们最好的"零显存兜底"**（GGUF 跑 CPU，把 8GB 显存全留给 TTS/翻译）；
**Fun-ASR-Nano 是"中文垂直领域 + 热词 + 方言口音"的特种兵**，但**它现在还没有时间戳**，配音阶段要慎用。

| 场景 | 选它 | 一句话理由 |
|---|---|---|
| 中英混合 / 中文口播（我们的主战场） | **Qwen3-ASR** | 30 语种 + 22 种中文方言口音，**单模型统一流式与离线**，官方实时 API，Apache-2.0 |
| 纯英文生肉、要最快最便宜 | Whisper large-v3-turbo | 英文最成熟、云端（Groq）极快，但中文弱、中英混说差、BGM 下易幻觉复读 |
| 中文为主、要省资源（尤其以后本地） | **SenseVoice-Small** | 非自回归，10s 音频 **70ms**（比 Whisper-Large 快 15 倍），有 llama.cpp/GGUF **纯 CPU** 版本 → 不占显存 |
| 中文垂直领域（教育/金融/远场高噪）、方言口音重 | Fun-ASR-Nano | 官方主打行业术语与抗幻觉，**热词 + ITN** 原生支持；代价是 800M、**无时间戳** |

---

## 1. 硬指标对比

| | **SenseVoice-Small** | **Fun-ASR-Nano-2512** | **Whisper large-v3-turbo** | **Qwen3-ASR 0.6B / 1.7B** |
|---|---|---|---|---|
| 参数 | ≈234M（与 Whisper-Small 相当） | 800M | 809M | 0.6B / 1.7B |
| 语种 | 50+ / 重点 zh、yue、en、ja、ko | Nano：中英日 + **7 大方言 + 26 种口音**；MLT-Nano：31 语种 | 99 | **30 语种 + 22 种中文方言口音** |
| 架构 | 非自回归（极快） | 端到端（通义实验室，数千万小时） | 编码-解码（turbo 解码层裁到 4） | 基于 Qwen3-Omni，单模型流式/离线统一 |
| **流式** | ❌ 无原生流式，靠 VAD 切句 + 整句识别 | ✅ 标称低延迟实时 | ❌ 需 SimulStreaming/LocalAgreement 伪装 | ✅ **官方支持**（仅 vLLM 后端；流式下不支持 batch 与时间戳） |
| **时间戳** | 靠 FunASR 的 VAD/标点侧提供 | ❌ **官方 TODO 尚未支持** | ✅ 原生词级时间戳 | ✅ 配套 **Qwen3-ForcedAligner-0.6B**（11 语种） |
| 热词/术语 | 弱（依赖 FunASR 侧） | ✅ **hotwords + ITN** | 弱（prompt 效果有限） | 较强（上下文/热词） |
| 延迟/吞吐 | 10s 音频 ≈70ms；比 Whisper-Large 快 15× | 未公布，标称实时 | 快（turbo），但需攒句 | 0.6B 在并发 128 时 **2000× 吞吐** |
| 显存（fp16/int8 粗估） | ~0.5GB / 有 **GGUF 纯 CPU** | ~1.6GB | int8 ≈1.5GB | 0.6B ≈1.3GB；1.7B ≈3.5GB |
| 特色 | 情感识别 + 声音事件（BGM/掌声/笑声） | 远场高噪 93%、歌词/Rap、抗幻觉 | 生态最成熟、工具链最多 | 歌唱/带 BGM 歌曲可转写；官方 SOTA 宣称 |
| 许可 | ⚠️ FunASR 自定义 MODEL_LICENSE | ✅ Apache-2.0 | ✅ MIT | ✅ Apache-2.0 |
| 云 API | 硅基流动等（`/audio/transcriptions`） | 魔搭/自建为主 | Groq / 硅基流动 / OpenAI（**最快最便宜**） | 阿里百炼：`qwen3-asr-flash` + **实时版** |

来源：[SenseVoice-Small 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)、[Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512)、[Fun-ASR-MLT-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-MLT-Nano-2512)、[Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)（实时 API 文档 <https://help.aliyun.com/zh/model-studio/qwen-real-time-speech-recognition>）、[whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)。

---

## 2. 用我们项目的判据打分

判据来自我们的真实需求：**中英双向、实时字幕、术语一致、以后要配音对齐、8GB 显存还要留给 TTS、现在只用云 key。**

| 判据（权重） | SenseVoice | Fun-ASR-Nano | Whisper-turbo | Qwen3-ASR |
|---|:--:|:--:|:--:|:--:|
| 中文识别质量（高） | ★★★★ | ★★★★★ | ★★★ | ★★★★★ |
| 英文识别质量（高） | ★★★★ | ★★★★ | ★★★★★ | ★★★★☆ |
| 中英混说 / 方言口音（高） | ★★★☆ | ★★★★★ | ★★☆ | ★★★★★ |
| 流式与低延迟（高） | ★★ | ★★★★ | ★★★ | ★★★★★ |
| 热词 / 术语一致性（中） | ★★ | ★★★★★ | ★★ | ★★★★ |
| 时间戳（中，P2 配音刚需） | ★★★ | ★★ | ★★★★ | ★★★★★ |
| 资源占用（中） | ★★★★★ | ★★★ | ★★★ | ★★★★ |
| 云端可得性（中，当前阶段） | ★★★★★ | ★★ | ★★★★★ | ★★★★ |
| 许可宽松度（低） | ★★★ | ★★★★★ | ★★★★★ | ★★★★★ |

**读表**：Qwen3-ASR 是唯一在"流式 + 时间戳 + 中英方言覆盖"上都不掉链子的；SenseVoice 赢在资源；Whisper 赢在英文和生态；Fun-ASR-Nano 赢在中文领域与热词，输在时间戳缺失。

---

## 3. 为什么这么排

### 3.1 Qwen3-ASR 主选的三条理由
1. **语言画像最贴我们**：B 站内容大量是"普通话带口音 + 中英夹杂 + 专有名词"，Qwen3-ASR 明确覆盖 22 种中文方言/口音和 30 语种，还支持歌唱/带 BGM 的歌曲转写——这正是 Whisper 的软肋（BGM 下容易幻觉复读）。
2. **单模型统一流式与离线**：我们今天的实现是"VAD 切句 → 整句识别"（延迟 = 句长 + 识别时间）。将来升级真流式时，**不用换模型**：0.6B 本地 + vLLM 就能开流式；云上也有官方实时 API。别的候选要么没有流式（SenseVoice、Whisper），要么流式是社区改造（Whisper 的 SimulStreaming）。
3. **有配套时间戳模型**：`Qwen3-ForcedAligner-0.6B` 提供 11 语种的时间戳预测。P2 配音要做"每句塞回原时间槽"和变速对齐，时间戳是刚需——Fun-ASR-Nano 官方 TODO 里明确"时间戳尚未支持"，SenseVoice 得靠 VAD 边界凑。

**代价（必须知道）**：0.6B 与 1.7B 差距明显（1.7B 才号称开源 SOTA、对标最强商业 API）；0.6B 优势是吞吐/成本。**这两档怎么选，必须用你的素材测**——见第 4 节。

### 3.2 Whisper large-v3-turbo：只当英文档，不当主力
- 优点很实在：英文最稳、99 语种、生态最成熟（faster-whisper/CT2 一行跑起来）、云端最快最便宜（Groq 上几乎是瞬时）。
- 缺点对我们的场景很致命：中文弱于专用中文模型；**中英混说经常切换错语言或整段丢**；BGM/音乐/静音段容易幻觉复读（字幕里冒出原文没有的句子，观感极差）；本身不是流式，需要攒句或用 LocalAgreement/SimulStreaming 策略伪装，实现复杂度一下子上去。
- 定位：`en→zh` 的**英文生肉专场** + 多语种兜底。

### 3.3 SenseVoice：我们最缺的那个角色——"不占显存的那一个"
我们的显存账本很紧（见 [03-架构设计](03-架构设计.md#4-显存账本8gb-硬预算)）：TTS 要 6GB、翻译 2B 要 2.3GB、ASR 再占 1.5GB 就爆了。
SenseVoice-Small 有**官方 llama.cpp/GGUF 运行时**（[SenseVoiceSmall-GGUF](https://huggingface.co/FunAudioLLM/SenseVoiceSmall-GGUF)），**纯 CPU、免 Python、自带 VAD**：10s 音频 70ms 的推理速度放在 CPU 上依然远超实时。
→ 也就是：**ASR 完全不上显卡，把 8GB 全留给 TTS 和翻译。** 这对我们这个项目是独一无二的价值。
代价：非自回归架构决定了它**不是流式模型**，适合我们现在这套"切句后整句识别"的管线；情绪/事件识别是额外彩蛋（可以拿"掌声/笑声/BGM"事件做字幕降噪）。

### 3.4 Fun-ASR-Nano：特种兵，不是全能选手
- 强项精准踩中"垂直领域"：教育/金融术语、远场高噪（93%）、7 大方言 + 26 种口音、歌词/Rap、抗幻觉；**hotwords + ITN 原生支持**——这和我们的"术语表"机制天然契合。
- 但两条硬伤：**时间戳官方还没支持**（P2 配音会卡住）；800M 体积比 SenseVoice 大 3.4 倍，却换不来流式之外的明显优势。
- 定位：中文课程/财经/纪录片这类"术语密集"内容的专项档；或者将来做本地部署时的"中文精修档"。

---

## 4. 我们最终的分档方案（不是"选一个"）

**云端档（现在就能跑）**
| 档位 | 模型 | 用途 |
|---|---|---|
| 默认档 | `qwen3-asr-flash`（阿里百炼） | 中英混合、方言口音、日常主用 |
| 英文快速档 | `whisper-large-v3-turbo`（Groq / 硅基流动） | 纯英文视频，延迟最低 |
| 省钱档 | `FunAudioLLM/SenseVoiceSmall`（硅基流动） | 中文为主、量大、成本敏感 |
| 术语档 | 待测：Fun-ASR-Nano（魔搭/自建） | 术语密集内容 |

**本地档（将来，8GB 显存必须精打细算）**
| 档位 | 模型 | 显存 |
|---|---|---|
| 主力 | **Qwen3-ASR-0.6B + vLLM（流式）** | ≈1.3GB |
| 兜底/省显存 | **SenseVoice-Small GGUF（CPU）** | **0 GB 显存** |
| 备用 | whisper-large-v3-turbo（faster-whisper int8） | ≈1.5GB |

已经把这些候选写进 [config.yaml](../config.yaml) 的 `asr.candidates`，三种调用风格（`transcriptions` / `chat_audio`）都在 [asr.py](../apps/engine/citty/asr.py) 里实现好了——**换模型只改配置，不改代码。**

---

## 5. 别听我的，用你自己的视频测（这就是 `citty ab` 的用途）

```powershell
# 1) 播着你要测的那个视频，录 40 秒系统声音
.\run.ps1 record 40

# 2) 三家（或你配的任意多家）并排跑同一段音频
.\run.ps1 ab out\sample.wav

# 3) 想算 CER/WER：把你听到的标准答案打成 txt（中文按字、英文按词）
.\run.ps1 ab out\sample.wav out\ref.txt
```

输出长这样（示例结构）：
```
■ qwen3-asr-flash    端到端 980ms | RTF 0.024 | CER(字) 4.2% | 转写: ...
■ sensevoice-small   端到端 610ms | RTF 0.015 | CER(字) 6.8% | 转写: ...
■ whisper-...-turbo  端到端 430ms | RTF 0.011 | WER(词) 3.1% | 转写: ...
```

**建议录 3 类样本**（各 30~40 秒），分别对应我们最常遇到的场景：
1. **英文科技/评测视频**（生肉，语速快、术语多）→ 看英文档谁赢；
2. **中文口播/教程**（普通话，可能带口音）→ 看中文档谁赢；
3. **带 BGM / 有掌声笑声的片段** → 看谁能不幻觉、不错乱（这一项通常 Whisper 会翻车，SenseVoice 的事件检测反而能帮上忙）。

**判据**（按我们项目的优先级）：
1. 错字/漏句位置是否致命（专名、数字单位、否定词）；
2. 中英混说时有没有整段丢失或语言搞错；
3. RTF 是否 < 0.5（要留出翻译的时间）；
4. 有没有幻觉复读；
5. 时间戳可用性（P2 才强依赖，现在可以先记着）。

---

## 6. 已知的坑（写进代码注释里那种）

| 坑 | 说明 | 应对 |
|---|---|---|
| Qwen3-ASR 流式限制 | 流式**仅 vLLM 后端**，且流式下**不支持 batch 与时间戳** | 字幕用流式；需要时间戳的场合（配音）切回离线 + ForcedAligner |
| SenseVoice / Whisper 都不是流式 | 延迟 = 句长 + 识别时间 | 我们现在的 VAD 切句已经能用；想再快就上 Qwen3-ASR 流式 |
| Fun-ASR-Nano 无时间戳 | 官方 TODO 未完成 | 不要放进配音链路；只用于字幕转写 |
| Whisper 幻觉 | BGM/音乐/静音段冒出原文没有的话 | 我们已有 VAD 过滤 + 空段跳过；再加"译文长度异常"检测 |
| 商用许可 | SenseVoice 是 FunASR 自定义许可（非 Apache），音色/模型条款需逐条核对 | 商用前过一遍 [02-模型与工具选型](02-模型与工具选型.md#6-许可证与合规速查) |
| 云端模型上下架 | 各平台模型列表随时变 | 全部走 adapter + 候选列表，一家断了立刻换另一家 |
| 采样率/声道 | 云端 `/audio/transcriptions` 对 16k 单声道 wav 最稳 | 我们的 `pcm_to_wav_bytes` 已经固定 16k 单声道 |

---

## 7. 今天就能做的三件事

**去哪拿 key（都已实测可访问，2026-10-06）**

| 优先级 | 平台 | 地址 | 能覆盖 |
|---|---|---|---|
| ① | 阿里云百炼（通义） | <https://bailian.console.aliyun.com/?apiKey=1> | `qwen3-asr-flash`（主档）+ 将来的实时 API；国内直连，需实名，新用户有免费额度 |
| ② | 硅基流动 SiliconFlow | <https://cloud.siliconflow.cn/account/ak> | `FunAudioLLM/SenseVoiceSmall`、`whisper-large-v3(-turbo)`；国内直连，注册送额度 |
| ③ | Groq | <https://console.groq.com/keys> | `whisper-large-v3-turbo`（英文最快最省）；国内需代理 |

拿到 key 后：
```powershell
Copy-Item D:\Citty\.env.example D:\Citty\.env
notepad D:\Citty\.env                       # 填进去，例如 DASHSCOPE_API_KEY=sk-xxx
# 然后把 config.yaml 里 asr.backend 从 mock 改成 openai_compat（或 chat_audio），
# 并让 asr.openai_compat 那段指向你要用的那家（base_url / model / api_key_env）

# 一条命令验证：key 有没有读到、鉴权通不通、调用形态对不对、真跑一句转写
python D:\Citty\tools\probe_asr_key.py --list
.\run.ps1 doctor
```

> ⚠️ **key 类型别搞错**：模型服务要的是**百炼控制台里的 API-KEY**（`sk-` 开头）。
> 阿里云账号的 **AccessKey ID / Secret** 是账号级凭据（能建资源、能花钱），**不是**这里要的东西，
> 也不要用它调模型 API——权限过大，一旦泄露后果完全不同量级。
> 拿不到百炼的，硅基流动注册更快（手机号 + 送额度），效果一样能用。

1. **先接一家跑起来**：中英通吃建议先上百炼的 `qwen3-asr-flash`；只想先验证速度就上 Groq 的 whisper-turbo。
2. **录 3 类样本跑 `citty ab`**：这会把"哪个 ASR 更适合我们"从争论变成一张表。
3. **把结论写回 `config.yaml`**：主档定下来，`candidates` 保留其余两家做备选——**架构上永远留一条退路**。

---

## 8. 多平台扩展（B 站 → YouTube）对选型的影响

我们的定位不是"B 站字幕工具"，而是**所有视频内容的翻译**，B 站只是第一个平台。这会不会改变结论？

**不会改变主档，但会改变架构的两处设计。**

### 8.1 语种画像变了，但 Qwen3-ASR 仍然覆盖得住
| | B 站 | YouTube |
|---|---|---|
| 主语言 | 中文（普通话 + 方言口音）、中英夹杂 | 英文为主，长尾语种极多（印地语、西语、葡语、阿语、印尼语…） |
| 音频质量 | 二创混音、BGM 多、生活音多 | 口播清晰的多，但也有 vlog 环境音 / 音乐区 / 多人对话 |
| 字幕可得性 | 多数视频没有可用字幕，**必须自研 ASR** | **大量视频自带人工/自动字幕** |

Qwen3-ASR 覆盖 30 语种 + 22 种中文方言口音，正好压住 YT 的"主流语种"（英/日/韩/西/法/德/葡/俄/阿/印地/泰/越/印尼…）；真正长尾的（北欧、小语种、非洲语言）交给 Whisper 的 99 语种兜底。**所以它是"多平台都能用"的主档，不是"B 站专用"。**

### 8.2 关键架构启示一：把「字幕来源」抽象成一档
YT 有现成字幕（人工上传的比自动的准得多），B 站多数没有。既然如此，架构上应该把

```
字幕来源 = ASR（我们识别）| platform_caption（平台自带）
```

做成和 `asr.backend` 并列的一档（就像我们现在 `mt.backend` 有 `bilibili_free` / `openai_compat` / `mock` 一样）。好处：
- YT 上**直接拉官方字幕**——又快又准又省 API 费用，ASR 只用在"没有字幕的频道/直播/生肉"上；
- 还能拿平台字幕当**对照校验**：自己识别的结果和官方字幕差太多，就是该报警的信号（这比任何离线 WER 评测都真实）；
- B 站将来若开放 CC 字幕，同一档直接复用。

### 8.3 关键架构启示二：ASR 该按「语言」选，不该按「平台」选
我们的选择逻辑应该是 **语言路由**，不是 if 平台：
```
中文内容   → Qwen3-ASR / SenseVoice（中文与方言最强）
英文内容   → Qwen3-ASR（够好）或 Whisper-turbo（最快最便宜）
长尾语种   → Whisper（99 语种）
术语密集   → Fun-ASR-Nano（hotwords）
```
也就是把 P3 阶段"用模型分类视频再路由翻译模型"的思路，**提前到 ASR 这一层先做一遍**——同一个 router 埋点可以同时服务 ASR 和 MT，成本几乎不增加。

### 8.4 YT 场景要额外防的两件事
1. **音乐/多人/长视频**：Whisper 在这类音频上幻觉复读的概率显著上升（会冒原文没有的句子）。我们的 VAD + 空段过滤能挡住一部分，还需要加"译文长度异常/重复率异常"检测。
2. **版权与合规**：给别人平台的视频做翻译字幕并分发，比对自家内容做要谨慎（见 [04-路线图与风险](04-路线图与风险.md) 的合规清单）。技术上要留"仅本地使用、不外发"的开关。

**一句话**：选 Qwen3-ASR 当主档这个决定，从"B 站专用"升级到"多平台通用"是**站得住的**；真正需要提前设计的不是模型，而是**字幕源抽象 + 语种路由**这两处架构。

---

## 9. 实测数据（第一轮，2026-10-06）

选型文档最怕只有别人的榜单。下面是在**本机、本 key、真实语音**上跑出来的第一批数字（硅基流动，
`POST /audio/transcriptions`，含网络上传时间）：

| 模型 | 中文样例 4.2s | 英文样例 15.1s | RTF（英文） | 转写质量 |
|---|---|---|---|---|
| `FunAudioLLM/SenseVoiceSmall` | 868ms | **2419ms** | **0.161** | 中文输出与另两家完全一致；英文仅标点差异 |
| `Qwen/Qwen3-ASR-1.7B` | 866ms | 2604ms | 0.173 | 同上，标点更规范（"Oh yeah, yeah."） |
| `XingChenAGI/XingChenASR-V3.2` | 1093ms | 4570ms | 0.304 | 输出几乎一致但**慢一倍**，暂不推荐 |

补充事实：**`Qwen/Qwen3-ASR-1.7B` 就在硅基流动上**（不需要百炼），所以"主档选 Qwen3-ASR"不需要额外平台。
中文样例（关于交易的财经内容）三家都给出同一句，说明这几家在**常规普通话上没有质量差异，只差速度**——
真正的分水岭要等**方言口音 / 中英混说 / 带 BGM** 的样本才能测出来（这正是第 5 节那三类样本的用意）。

### 9.1 顺带的重要发现：整个 P2/P3 路线图都能走云端
硅基流动的模型列表里还有几个"早点知道能省很多事"的：

| 模型 | 对应我们哪一步 | 意义 |
|---|---|---|
| `tencent/Hunyuan-MT-7B` | **P3 的腾讯 MT 路由** | 你当初想接的"腾讯更适合科技类"的那个模型，**云端直接可调，不用下载** |
| `FunAudioLLM/CosyVoice2-0.5B`、`fnlp/MOSS-TTSD-v0.5` | **P2 配音** | TTS 也能云上跑，**不必为了配音下载 IndexTTS-2.5（几个 GB）** |
| `Qwen/Qwen3-Omni-30B-A3B-*` | 多模态（视频理解/封面/场景） | 将来做"按视频类型路由"时的现成选项 |
| `XingChenAGI/XingChenASR-Diarize-V3.0` | 多人对话 | 带说话人分离，访谈/座谈类内容必要时可用 |

**结论修正**：原计划里"P2 必然要下载模型"这个假设**不成立**——配音、腾讯 MT、多模态路由都可以先用云 API
验证效果，等真正确认要本地化、再考虑下载权重。这条对整个项目的磁盘/显存压力是决定性的好消息。

### 9.2 现实约束：额度
硅基流动新账号需要**实名认证**才会发放免费额度；实测未领额度时所有模型（含纯文本）返回
`402 account balance is insufficient`。**充值 ¥10 就足够跑很久字幕**（ASR 按音频秒计费，字幕场景量很小）。
在此之前，可以用 `tools/mock_asr_server.py` 在完全零成本的前提下验证除 ASR 之外的整条链路。
