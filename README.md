# kikitori（聞き取り）

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | **English** | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

*This is a translation of the Traditional Chinese [README.zh-TW.md](README.zh-TW.md). If the two differ, README.zh-TW.md prevails. It was translated with AI assistance; corrections are welcome via [issues](https://github.com/alianlbj23/kikitori/issues).*

kikitori listens to the audio your computer is playing (or your microphone), transcribes it in real time, translates it into the language you choose, and shows it as bilingual subtitles floating on your screen.
Speech recognition (Whisper) and translation (local Ollama) both run on your own computer: audio and text are never uploaded to any cloud service.

The main use case is **watching Japanese VTuber livestreams** (with BGM, game sound effects and livestream slang); the recommended models and parameters were tuned from this project's own measurements.
Any other combination of the 20 supported languages works too.

- Platforms: Windows 10/11 x64 (main tested platform), Linux x64, macOS Apple Silicon
- License: MIT (models have their own licenses, see "License")
- Current version: 0.2.0 ([release notes](docs/release-notes/v0.2.0.md))

## Features

| Feature | Description |
|---|---|
| Video translation | Captures the audio the system is playing (Windows WASAPI loopback / Linux PulseAudio monitor / macOS ScreenCaptureKit) and translates the listening language into the subtitle language |
| Live interpreting | Captures the microphone; when you speak in "My language" it is translated into the "Other person's language", and when they speak it is translated into yours |
| Two-pass subtitles: fast first, accurate after | While a sentence is still being spoken, a faster model produces "live subtitles"; once it ends, a more accurate model recomputes "final subtitles" that replace them |
| Stable live subtitles | The beginning of a live subtitle that will no longer change is shown normally, and the tail that is still changing is shown faded; the translation no longer flickers along with the tail |
| Any combination of 20 languages | Pick the listening language (or "Detect automatically") and the subtitle language from 20 languages, see "Supported languages" |
| 13 interface languages | Follows the operating system by default; can be changed on the Settings page |
| Show originals only | Turn off "Show translation" to see only the original: no Ollama needed, no GPU used; can be switched while running |
| Furigana | Shows readings above the kanji of Japanese originals (SudachiDict, about 69 MiB, downloaded only the first time you turn it on) |
| Hardware-based model recommendations | Detects CPU, memory, NVIDIA GPU and Ollama and picks a model combination automatically; missing models are downloaded only the first time you press "Start" |
| Glossary | 53 built-in Japanese livestream terms (赤スパ, 推し, メン限…) plus easily confused Chinese–Japanese words for Chinese-to-Japanese; you can also add your own entries and import/export CSV |
| Transcript export | SRT, VTT, TXT, CSV; can be set to save automatically each time a session ends |
| OBS text file | Writes the current subtitles to a text file for the "Read from file" option of an OBS "Text (GDI+)" source; no network port is opened |
| Subtitle window | Transparent, always on top, click-through; font size, background, position, number of lines, minimum display time per line and idle fade-out time are all adjustable |
| Status bar | Volume meter, latency (median from end of speech to final subtitle); warns when recognition can't keep up or there has been no sound for 15 seconds |
| Model management | Download speech recognition models, view their disk usage and delete them; downloads translation models into Ollama for you |
| Auto-update | Checks GitHub Releases at startup, downloads the update, verifies its signature and installs it |

## Supported languages

Listening and subtitle languages (20, any combination):

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- Traditional Chinese subtitles are converted with OpenCC `s2twp` to Taiwan-standard Traditional characters and Taiwanese usage (e.g. 视频 → 影片). The conversion only runs when the translation contains Simplified characters, and it does not change 「台灣」 to 「臺灣」.
- When the listening and subtitle languages are the same, nothing is translated (between Traditional and Simplified Chinese, only the script is converted).
- Speech recognition quality varies by language. When a model's error rate for a language is above 20% (e.g. Thai, Arabic and Hindi with large-v3-turbo, Vietnamese with large-v3), the recommendation notes warn that it recognizes that language poorly; for Thai, Arabic and Hindi on GPUs with 8 GB or more, live subtitles also switch to large-v3.

Interface languages (13): 繁體中文（台灣）, 简体中文, English, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil), Русский, Tiếng Việt, ไทย, Bahasa Indonesia.
Arabic can be used as a listening or subtitle language, but there is no Arabic interface (the interface does not support right-to-left layouts).

## Recommended combinations (by hardware)

kikitori detects and applies these automatically; the table only shows what you will get. They are based on this project's evaluations (`experiments/vtuber_eval/`, `experiments/translate_eval/`); the rules and formulas are in [`docs/specs/hardware.md`](docs/specs/hardware.md).

| Your computer | Live subtitles | Final subtitles | Translation (Ollama) |
|---|---|---|---|
| NVIDIA 16 GB or more | large-v3-turbo (float16) | large-v3 (float16) | qwen3.5:9b |
| NVIDIA 12 GB (e.g. RTX 5070) | large-v3-turbo (int8_float16) | large-v3 (int8_float16) | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo (small when VRAM is right at the 8 GB minimum) | large-v3 (precision chosen by VRAM) | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA under 4 GB | base | small | qwen3.5:4b |
| No NVIDIA GPU, RAM ≥ 16 GB, 5 or more cores | base (CPU) | small (CPU) | qwen3.5:4b |
| No NVIDIA GPU, less RAM or 4 cores or fewer | tiny (CPU) | base (CPU) | qwen3.5:4b |
| Mac (Apple Silicon) | base (CPU) | small (CPU) | Depends on unified memory size (qwen3.5:9b at 16 GB) |

- In our measurements, int8_float16 and float16 give the same recognition quality (no significant difference), but int8_float16 uses about half the VRAM; when the GPU is shared with the translation model, kikitori prefers to let the translation model be one size larger.
- Speech recognition model downloads: large-v3-turbo 1.62 GB, large-v3 3.09 GB, small 487 MB, base 148 MB, tiny 78 MB. Translation models: qwen3.5:9b about 6.6 GB, qwen3.5:4b about 3.3 GB.
- NVIDIA GPUs need cuBLAS 12 (about 570 MB). kikitori does not bundle it with the installer; after you accept the NVIDIA EULA on the Models page it downloads with one click. Without it, the CPU is used.
- When the translation server is on another computer (remote), no translation model is chosen automatically; pick one yourself on the Models page.
- For translation, Qwen3.5 (Apache-2.0) is always recommended first: 9B if there is enough VRAM, otherwise 4B. With a GPU of 4 GB or less, or no NVIDIA GPU, it is also qwen3.5:4b; Ollama runs the part that doesn't fit on the CPU, which is slower (translation latency in this case has not been measured yet).
- Tencent HY-MT has regional restrictions and can only be recommended after you tick the confirmation on the Settings page that you don't use it in the EU, the UK or South Korea; Sakura (non-commercial license) is never recommended and can only be chosen manually.
- Recognition speed on macOS and Linux and translation speed on the CPU have not been measured yet (see "Known limitations").

## Getting started

1. Download the installer from [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) (installation notes for each platform are on the Release page: SmartScreen/Gatekeeper warnings for unsigned builds, fuse3 and fonts on Linux).
2. To translate, install [Ollama](https://ollama.com/download) and keep it running in the background. If kikitori can't detect it, it offers "Install Ollama" or "Show originals only".
3. Open kikitori. On first launch it automatically applies the recommended combination for your hardware.
4. Under "Languages" on the Settings page, choose the listening language and the subtitle language. This only shows the recommended combination and download size; **nothing is downloaded yet**.
5. Choose the audio source and press "Start". Missing speech recognition and translation models (with Ollama) are downloaded first, and subtitles start automatically when done.

### Show originals only

Use the "Show translation" switch in the Transcript page toolbar or in the "Subtitles" section of the Settings page (or the shortcut Ctrl+Alt+J). When it is off:

- the engine does not connect to the translation server at all, Ollama is not needed, and no GPU is used for a translation model;
- the subtitle window shows the original in the main subtitle's font size and color;
- switching while running applies immediately; lines already shown are not translated afterwards.

### Furigana

"Show furigana over Japanese originals" on the Settings page: when the listening language is Japanese, hiragana readings are shown above the kanji of the original (okurigana are not annotated again). The first time you turn it on, SudachiDict core (Apache-2.0, about 69 MiB) is downloaded; its disk usage can be viewed and it can be deleted on the Models page. It only affects the display; exported subtitles are unchanged. Design: [`docs/specs/furigana.md`](docs/specs/furigana.md).

### Glossary

"Glossary" on the Settings page:

- **Built-in livestream terms** (on by default): used when the original is Japanese and the subtitles are Traditional Chinese, Simplified Chinese, English or Korean; for English subtitles, for example, スパチャ → Super Chat, メン限 → members-only, 切り抜き → clip. Only terms that actually appear in the sentence's original are put into the translation prompt (at most 8 per sentence).
- **Easily confused Chinese–Japanese words** (follow the built-in list's switch): when the original is Chinese and the subtitles are Japanese, the prompt flags words written the same or similarly in Chinese and Japanese but with different meanings, e.g. 便當 → 弁当, 節目 → 番組, 老婆 → 妻; Chinese-to-Japanese also gives the model its instructions in Chinese, so it is less likely to read Chinese with Japanese meanings.
- **Custom entries**: original, translation and the subtitle language they apply to (or "All languages"), up to 500 entries, for any listening language; for the same spelling they take priority over the built-in list. Matching ignores case and full-width/half-width differences.
- You can import/export CSV (UTF-8, separated by commas, semicolons or tabs; files saved by Excel can be read too). After you press "Save glossary", changes take effect the next time you start subtitles.

### Transcript export

"Export" on the Transcript page: SRT, VTT, TXT, CSV, with the content set to "Original and translation", "Original only" or "Translation only". Only final subtitles are exported (live subtitles are still changing).
Files are saved to `Documents/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<mode>.<extension>`, with timecodes counted from when subtitles started.
On the Settings page you can turn on "Save automatically when a session ends (SRT and TXT)"; after you press "Stop", it waits for the final subtitle of the last sentence (up to 2 seconds) before saving.

### OBS text file

Settings page "Transcripts and OBS" → "Write subtitles to a text file (for OBS)": original, translation or both.
kikitori overwrites `Documents/kikitori/obs/subtitle.txt` with the lines currently in the subtitle window (UTF-8, atomic writes, at most 10 times per second);
in OBS, add a "Text (GDI+)" source, tick "Read from file" and point it at this file. The file is emptied when subtitles end, fade out when idle, or are cleared.
No network port is opened, so there is no approach using an OBS "Browser" source.

### Global shortcuts

| Windows / Linux | macOS | Action |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | Toggle click-through for the subtitle window |
| Ctrl+Alt+J | ⌘+Option+J | Toggle "Show translation" |
| Ctrl+Alt+U | ⌘+Option+U | Clear the subtitle window (and the OBS text file); the transcript is not affected |

A shortcut taken by another program is marked as unavailable on the Transcript page; the other shortcuts are not affected. On Linux Wayland, global shortcuts may not be registered at all.

## Privacy and offline use

- Audio, recognition results and translations are processed only on your computer. Translation goes to local Ollama by default (`http://localhost:11434/v1`); if you change the translation service to another URL, the subtitle text is sent there.
- The only network access is: downloading models (speech recognition models from Hugging Face, translation models via Ollama, GPU components from PyPI, all only when you press Start or download), and checking for updates at startup (GitHub Releases). Once all models are downloaded, subtitles themselves work fully offline.
- The API key is read only from an environment variable (the settings store only the variable name, `KIKITORI_TRANSLATE_API_KEY` by default) and is never written to any file.
- "Copy diagnostics" on the "About" page only copies to the clipboard; it masks keys, credentials in URLs and user names, and lists only the number of glossary entries.
- The engine and the desktop app communicate only through standard input/output; no network port is opened.

## What's new in 0.2.0

Summary (for full details and upgrade notes see [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)):

- Any combination of 20 languages, 13 interface languages, "Show originals only", furigana for Japanese
- Models re-selected for Japanese VTuber livestreams (GPU: live large-v3-turbo, final large-v3; translation temperature 0)
- Built-in livestream glossary and custom entries
- Stable live subtitles, volume meter and latency, warning when recognition falls behind
- Export to SRT/VTT/TXT/CSV, OBS text file, reading pace settings
- Missing models downloaded only at start, model disk usage and deletion
- New shortcuts Ctrl+Alt+J/U, "Copy diagnostics"
- More accurate Chinese-to-Japanese (instructions written in Chinese and a list of easily confused Chinese–Japanese words), translation model chosen from a drop-down list, and closing kikitori also unloads the translation model from local Ollama to free VRAM
- Faster "Start" (12 GB recommended combination with translation: right after launch about 11 → 6 seconds, afterwards about 7.8 → 2.4 seconds), update prompts show the release notes
- Fixes: models left on the GPU when opening the device failed, each subtitle session using about 17 MiB more VRAM, main window memory growing steadily during long runs

## Known limitations

- **Fully tested only on Windows (RTX 5070)**. Real-device capture on macOS and Linux, and translation speed on Apple Silicon and the CPU, have not been measured; Linux has only been verified in a Docker container.
- **The new recommended combination has been measured end to end only on an RTX 5070 (12 GB)**: with turbo + large-v3 (int8_float16) and qwen3.5:9b running together, the whole card peaks at 10.8 GB, live subtitle latency is about 0.31 seconds, and end of speech to final subtitle is about 0.92 seconds (medians, see [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)).
- **The recognition accuracy evaluation material is not VTuber content**: it is everyday YouTube speech (YODAS), read speech and TV audio, mixed with BGM; there is no data from singing streams, screaming or multi-person collabs. Singing passes through voice activity detection and may produce odd subtitles.
- Languages other than English have only public benchmarks and non-speech hallucination tests, with no measurements on real speech; the hallucination phrase list has not been reviewed by native speakers.
- Some stock closing phrases (e.g. 「チャンネル登録よろしくお願いします」) are filtered out as whole-sentence Whisper hallucinations, even when the streamer really says them.
- Translation requires installing Ollama separately; with OpenAI-compatible servers other than Ollama, you have to install the models yourself.
- Long-running memory stress tests were done on Windows (engine flat over a 20-minute session, main window +0.7 MiB/min over 22 minutes); macOS and Linux have not been tested.
- Only one subtitle session can run at a time; it does not reconnect automatically after a device is unplugged.
- Not supported: click-to-look-up words, capturing audio from a single app only, singing stream mode, speaker diarization, OBS "Browser" source (for the reasons see the "Not doing for now" (先不做) section of [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md) (in Chinese) and [`docs/adr/`](docs/adr/)).
- The installers have no paid code signing (Windows SmartScreen and macOS Gatekeeper will warn).

## License

kikitori itself is released under the MIT license ([LICENSE](LICENSE)). The full license texts of third-party components are in the installation directory and can be opened from the "About" page.

Models are not distributed with the installer; their own licenses apply when you download them:

| Model | License | Notes |
|---|---|---|
| Whisper (CTranslate2 conversion) | MIT | — |
| Silero VAD (bundled with the app) | MIT | — |
| Qwen3.5 (default translation model) | Apache-2.0 | — |
| Tencent HY-MT1.5 | Tencent HY Community License | **Not applicable in the EU, the UK or South Korea**; kikitori does not recommend it by default, and your region must be confirmed on the Settings page |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **Commercial use prohibited**; never recommended, can only be chosen manually |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | Must be accepted before download |

## For developers

- Architecture: [docs/architecture.md](docs/architecture.md); module specs: [docs/specs/](docs/specs/); learning guide: [docs/learning/](docs/learning/README.md)
- Evaluations and experiments: `experiments/*/README.md`; research reports: `reports/`

```text
uv sync                                       Install Python dependencies
uv run python -m unittest discover -s tests   Python tests
uv run ruff check                             Lint
uv run kikitori                               Interactive menu (start the engine, list audio devices…)
cd app && npm ci && npm run menu              Desktop app development menu (requires Node and Rust)
cd app && npm test && npx tsc --noEmit        Frontend tests and type checking
```
