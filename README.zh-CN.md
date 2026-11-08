# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | **简体中文** | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*本文是繁体中文 [README.zh-TW.md](README.zh-TW.md) 的译本；两者内容不一致时，以 README.zh-TW.md 为准。本文借助 AI 翻译，欢迎通过 [issues](https://github.com/alianlbj23/kikitori/issues) 指正。*

kikitori 收听你电脑正在播放的声音（或麦克风），实时识别成文字、翻译成你选的语言，以悬浮在屏幕上的双语字幕显示。
语音识别（Whisper）与翻译（本机 Ollama）都在你自己的电脑上运行：声音与文字不会上传到任何云服务。

主要场景是**看日语 VTuber 直播**（有 BGM、游戏音效、直播用语），模型推荐与参数都依本项目的实测调整过；
20 种语言之间的其他组合也能用。

- 平台：Windows 10/11 x64（主要验证平台）、Linux x64、macOS Apple Silicon
- 许可：MIT（各模型另有许可，见“许可”）
- 当前版本：0.2.0（[更新内容](docs/release-notes/v0.2.0.md)）

## 功能

| 功能 | 说明 |
|---|---|
| 视频翻译 | 捕获系统播放的声音（Windows WASAPI loopback／Linux PulseAudio monitor／macOS ScreenCaptureKit），把收听语言翻译成字幕语言 |
| 实时口译 | 捕获麦克风；你说“我的语言”时译成对方的语言，对方说话时译成你的语言 |
| 先快后准的双路径字幕 | 句子还在说时，先用较快的模型出“实时字幕”；说完后用较准的模型重算“完整字幕”替换它 |
| 稳定的实时字幕 | 实时字幕中已经不会再变的开头正常显示、还在变动的结尾以浅色显示；译文不再跟着结尾一直闪烁 |
| 20 种语言任意组合 | 收听语言（可选“自动检测”）与字幕语言从 20 种语言中任选，见“支持的语言” |
| 13 种界面语言 | 默认跟随操作系统，可在设置页切换 |
| 只显示原文 | 关闭“显示翻译”就只看原文：不需要 Ollama、不占 GPU；运行中也能切换 |
| 振假名 | 在日文原文的汉字上标注读音（SudachiDict，第一次打开时才下载约 69 MiB） |
| 按硬件推荐模型 | 检测 CPU、内存、NVIDIA 显卡与 Ollama，自动选择模型组合；第一次点击“开始”时才下载缺少的模型 |
| 术语表 | 内置 53 个日语直播用语（赤スパ、推し、メン限…）与中译日的中日易错词，也可以自定义词条、导入／导出 CSV |
| 导出字幕记录 | SRT、VTT、TXT、CSV；可设置每次结束时自动保存 |
| OBS 文本文件 | 把当前字幕写入文本文件，供 OBS“文本 (GDI+)”来源的“从文件读取”使用；不开放网络端口 |
| 字幕窗口 | 透明、置顶、可鼠标穿透；字号、背景、位置、句数、每句至少显示多久、空闲多久淡出都可调 |
| 状态栏 | 音量表、延迟（说完到完整字幕的中位数）；识别跟不上或 15 秒没有声音时提醒 |
| 模型管理 | 下载、查看磁盘占用、删除识别模型；替你在 Ollama 下载翻译模型 |
| 自动更新 | 启动时检查 GitHub Releases，下载并验证签名后安装 |

## 支持的语言

收听语言与字幕语言（20 种，任意组合）：

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- 繁体中文字幕用 OpenCC `s2twp` 转换为台湾正体与台湾用语（例如 视频 → 影片），只在译文出现简体字时才转换，不会把“台灣”改成“臺灣”。
- 收听与字幕是同一种语言时不翻译（繁中与简中之间只做字形转换）。
- 语音识别的质量因语言而异。模型对某个语言的错误率超过 20% 时（例如 large-v3-turbo 的泰语、阿拉伯语、印地语，large-v3 的越南语），推荐说明会提示“质量较差”；泰语、阿拉伯语、印地语在 8 GB 以上的显卡上实时字幕也改用 large-v3。

界面语言（13 种）：繁體中文（台灣）、简体中文、English、日本語、한국어、Español、Français、Deutsch、Português (Brasil)、Русский、Tiếng Việt、ไทย、Bahasa Indonesia。
阿拉伯语可以作为收听或字幕语言，但没有阿拉伯语界面（界面不支持从右到左的布局）。

## 推荐组合（按硬件）

kikitori 会自动检测并应用，下表只是让你知道会得到什么。依据是本项目的评测（`experiments/vtuber_eval/`、`experiments/translate_eval/`），规则与公式见 [`docs/specs/hardware.md`](docs/specs/hardware.md)。

| 你的电脑 | 实时字幕 | 完整字幕 | 翻译（Ollama） |
|---|---|---|---|
| NVIDIA 16 GB 以上 | large-v3-turbo（float16） | large-v3（float16） | qwen3.5:9b |
| NVIDIA 12 GB（例如 RTX 5070） | large-v3-turbo（int8_float16） | large-v3（int8_float16） | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo（显存刚好是 8 GB 下限时改用 small） | large-v3（按显存选择精度） | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA 不到 4 GB | base | small | qwen3.5:4b |
| 没有 NVIDIA GPU，RAM ≥ 16 GB、5 核以上 | base（CPU） | small（CPU） | qwen3.5:4b |
| 没有 NVIDIA GPU，RAM 较少或 4 核以下 | tiny（CPU） | base（CPU） | qwen3.5:4b |
| Mac（Apple Silicon） | base（CPU） | small（CPU） | 按统一内存大小（16 GB 为 qwen3.5:9b） |

- 精度 int8_float16 与 float16 的识别质量实测相同（差异不显著），只是显存约减半；与翻译模型共用显卡时，kikitori 优先让翻译模型用大一级的。
- 识别模型下载量：large-v3-turbo 1.62 GB、large-v3 3.09 GB、small 487 MB、base 148 MB、tiny 78 MB。翻译模型：qwen3.5:9b 约 6.6 GB、qwen3.5:4b 约 3.3 GB。
- NVIDIA GPU 需要 cuBLAS 12（约 570 MB）。kikitori 不随安装包附带；在“模型”页同意 NVIDIA EULA 后一键下载。没装时会改用 CPU。
- 翻译服务器在其他电脑（远程）时，不会自动选择翻译模型，请在“模型”页自己选。
- 翻译一律优先推荐 Qwen3.5（Apache-2.0）：显存够就用 9B，否则用 4B。显卡 4 GB 以下或没有 NVIDIA GPU 时也是 qwen3.5:4b，Ollama 会把放不下的部分放到 CPU 运行，速度较慢（这种情况的翻译延迟还没有实测）。
- 腾讯 HY-MT 有地区限制，只有你在设置页勾选“我确认我不在欧盟、英国或韩国使用 kikitori”后才可能被推荐；Sakura（非商用许可）永远不会被推荐，只能手动选择。
- macOS 与 Linux 的识别速度、CPU 上的翻译速度都还没有实测（见“已知限制”）。

## 开始使用

1. 从 [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) 下载安装包（各平台的安装提示写在 Release 页面：未签名的 SmartScreen／Gatekeeper 警告、Linux 的 fuse3 与字体）。
2. 需要翻译的话，安装 [Ollama](https://ollama.com/download) 并让它在后台运行。kikitori 检测不到时会提示“安装 Ollama”或改用“只显示原文”。
3. 打开 kikitori。第一次启动会按硬件自动应用推荐组合。
4. 在设置页的“语言”中选择收听语言与字幕语言。选择后只会显示推荐组合与下载大小，**不会立即下载**。
5. 选择音频来源，点击“开始”。缺少的识别模型与翻译模型（使用 Ollama 时）会先下载，完成后自动开始。

### 只显示原文

字幕记录页工具栏与设置页“字幕”区块的“显示翻译”开关（或快捷键 Ctrl+Alt+J）。关闭时：

- 引擎完全不连接翻译服务器，不需要安装 Ollama，也不为翻译模型占用 GPU；
- 字幕窗口把原文改为主要字幕的字号与颜色；
- 运行中切换即时生效，已显示的句子不会补译。

### 振假名

设置页“在日文原文上标振假名”：收听语言是日语时，在原文的汉字上方标注平假名读音（送り仮名不重复标注）。第一次打开时下载 SudachiDict core（Apache-2.0，约 69 MiB），“模型”页可以查看占用并删除。只影响显示，导出的字幕不变。设计见 [`docs/specs/furigana.md`](docs/specs/furigana.md)。

### 术语表

设置页“术语表”：

- **内置直播用语表**（默认开启）：原文是日语、字幕是繁中／简中／英文／韩文时使用，例如 スパチャ → 超级留言、メン限 → 会员专属、切り抜き → 切片。只有这句原文实际出现的词才会放进翻译提示词（每句最多 8 条）。
- **中日易错词**（跟随内置表的开关）：原文是中文、字幕是日文时，提示中文与日文写法相同或相似、意思不同的词，例如 便当 → 弁当、节目 → 番組、老婆 → 妻；中译日也改用中文给模型写指示，减少模型按日文的意思理解中文。
- **自定义词条**：原文、译文、适用的字幕语言（或“全部语言”），最多 500 条，适用于任何收听语言；写法相同时优先于内置表。匹配不区分大小写与全角／半角。
- 可以导入／导出 CSV（UTF-8，逗号、分号或 Tab 分隔，Excel 保存的文件也能读取）。点击“保存术语表”后，下一次开始字幕时生效。

### 导出字幕记录

字幕记录页的“导出”：SRT、VTT、TXT、CSV，内容可选“原文与译文”“仅原文”“仅译文”。只导出完整字幕（实时字幕还在变动）。
文件保存在 `文档/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<模式>.<扩展名>`，时间码从开始字幕时算起。
设置页可以开启“每次结束时自动保存（SRT 和 TXT）”；点击“停止”后会等最后一句的完整字幕送达（最多 2 秒）再保存。

### OBS 文本文件

设置页“字幕记录与 OBS”→“将字幕输出到文本文件（供 OBS 使用）”：原文、译文或两者。
kikitori 把字幕窗口当前的句子覆盖写入 `文档/kikitori/obs/subtitle.txt`（UTF-8，原子写入，每秒最多 10 次）；
在 OBS 中添加“文本 (GDI+)”来源，勾选“从文件读取”并指向这个文件。字幕结束、空闲淡出或清除字幕时文件会被清空。
不开放任何网络端口，所以没有使用 OBS“浏览器”来源的做法。

### 全局快捷键

| Windows／Linux | macOS | 操作 |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | 切换字幕窗口的鼠标穿透 |
| Ctrl+Alt+J | ⌘+Option+J | 切换“显示翻译” |
| Ctrl+Alt+U | ⌘+Option+U | 清除字幕窗口（与 OBS 文本文件）；字幕记录不受影响 |

被其他程序占用的快捷键会在字幕记录页标示“无法使用”，不影响其他快捷键。Linux Wayland 可能完全无法注册全局快捷键。

## 隐私与离线

- 声音、识别结果与译文都只在你的电脑上处理。翻译默认发送到本机的 Ollama（`http://localhost:11434/v1`）；如果你把翻译服务改成其他地址，字幕文字会发送到那里。
- 会联网的只有：下载模型（识别模型来自 Hugging Face、翻译模型经由 Ollama、GPU 组件来自 PyPI，都是你点击开始或下载时才发生）、启动时检查更新（GitHub Releases）。模型都下载好之后，字幕本身可以完全离线使用。
- API 密钥只从环境变量读取（设置里只保存变量名，默认 `KIKITORI_TRANSLATE_API_KEY`），不写入任何文件。
- “关于”页的“复制诊断信息”只复制到剪贴板，会遮蔽密钥、网址中的账号密码与用户名，术语表只列出条数。
- 引擎与桌面程序之间只通过标准输入输出通信，不开放网络端口。

## 0.2.0 新功能

摘要（完整说明与升级注意事项见 [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)）：

- 20 种语言任意组合、13 种界面语言、只显示原文、日文振假名
- 针对日语 VTuber 直播重新选择模型（GPU：实时 large-v3-turbo、完整 large-v3；翻译温度 0）
- 内置直播用语术语表与自定义词条
- 稳定的实时字幕、音量表与延迟、识别落后提醒
- 导出 SRT／VTT／TXT／CSV、OBS 文本文件、阅读节奏设置
- 开始时才下载缺少的模型、模型磁盘占用与删除
- 新快捷键 Ctrl+Alt+J／U、“复制诊断信息”
- 中译日更准确（用中文写的指示与中日易错词表）、翻译模型改为下拉菜单、关闭 kikitori 时一并卸载本机 Ollama 的翻译模型以释放显存
- 点击“开始”更快（12 GB 推荐组合含翻译：刚打开时约 11 → 6 秒、之后约 7.8 → 2.4 秒）、更新提示显示版本说明
- 修复：打开设备失败时模型留在 GPU、每次开始字幕多占约 17 MiB 显存、长时间运行时主窗口内存持续增长

## 已知限制

- **只在 Windows（RTX 5070）上完整实测**。macOS 与 Linux 的实机捕获、Apple Silicon 与 CPU 上的翻译速度都没有测量过；Linux 只在 Docker 容器中验收过。
- **新推荐组合只在 RTX 5070（12 GB）上端到端测量**：turbo＋large-v3（int8_float16）与 qwen3.5:9b 同时运行时整张卡最高 10.8 GB、实时字幕延迟约 0.31 秒、说完到完整字幕约 0.92 秒（中位数，见 [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)）。
- **识别准确度的评测素材不是 VTuber**：用的是 YouTube 一般口语（YODAS）、朗读与电视语音，加上混入的 BGM；没有歌回、尖叫、多人联动的数据。歌声会穿过人声检测，可能出现奇怪的字幕。
- 英文以外的语言只有公开基准与非语音的幻觉测试，没有在真实语音上实测；幻觉短语表没有经过母语者审阅。
- 有些片尾套话（例如“チャンネル登録よろしくお願いします”）会被当成 Whisper 幻觉整句过滤掉，即使主播真的说了。
- 需要另外安装 Ollama 才能翻译；Ollama 以外的 OpenAI 兼容服务器只能自己安装模型。
- 长时间运行的内存压力测试在 Windows 上做过（引擎 20 分钟 session 持平、主窗口 22 分钟 +0.7 MiB／分钟）；macOS、Linux 没有测试。
- 同一时间只能有一个字幕 session；设备被拔掉后不会自动重连。
- 不支持：点词查询、只捕获单个 App 的声音、歌回模式、说话人分离、OBS“浏览器”来源（原因见 [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md)“先不做”与 [`docs/adr/`](docs/adr/)）。
- 安装包没有付费代码签名（Windows SmartScreen、macOS Gatekeeper 会警告）。

## 许可

kikitori 本身以 MIT 许可发布（[LICENSE](LICENSE)）。第三方组件的许可全文在安装目录，可从“关于”页打开。

模型不随安装包分发，由你下载时适用各自的许可：

| 模型 | 许可 | 注意 |
|---|---|---|
| Whisper（CTranslate2 转换版） | MIT | — |
| Silero VAD（随程序附带） | MIT | — |
| Qwen3.5（默认翻译模型） | Apache-2.0 | — |
| 腾讯 HY-MT1.5 | Tencent HY Community License | **不适用于欧盟、英国、韩国**；kikitori 默认不推荐，需在设置页确认所在地区 |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **禁止商业用途**；不会被推荐，只能手动选择 |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | 下载前需同意 |

## 给开发者

- 架构：[docs/architecture.md](docs/architecture.md)；各模块规格：[docs/specs/](docs/specs/)；学习指南：[docs/learning/](docs/learning/README.md)
- 评测与实验：`experiments/*/README.md`；调查报告：`reports/`

```text
uv sync                                       安装 Python 依赖
uv run python -m unittest discover -s tests   Python 测试
uv run ruff check                             Lint
uv run kikitori                               交互菜单（启动引擎、列出音频设备…）
cd app && npm ci && npm run menu              桌面程序的开发菜单（需要 Node 与 Rust）
cd app && npm test && npx tsc --noEmit        前端测试与类型检查
```
