"""端到端實測：本機音軌 → FileSource（即時速度）→ Silero VAD → ASR → 本機 Ollama 翻譯。

暫時性實驗，不屬於正式程式碼。以互動選單（input()）選擇 ASR 模型與翻譯模型，
印出字幕串流，並統計每句的即時與完整延遲：

- 首個即時字幕延遲：句子起點（含 200 ms 前置緩衝）到第一則即時字幕送出。
- 即時字幕延遲：每則即時字幕送出時，距它所辨識音訊結尾的時間。
- 完整字幕延遲：完整字幕送出時，距說話結束（裁掉句尾停頓後的終點）的時間；
  其中包含句尾判斷必須等的靜音（media 0.6 s − 保留的 0.2 s）。

音訊位置換算成牆鐘時間以「FileSource.start() 的時刻 + 樣本數 / 16000」計算。
執行：uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py
"""

import json
import statistics
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.asr.types import Transcript
from kikitori.capture import FileSource
from kikitori.config import Settings
from kikitori.pipeline import (
    Pipeline,
    PipelineComponents,
    SubtitleUpdate,
    default_transcript_filter,
    factory,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACK = ROOT / "experiments" / "pipecat_vad" / "data" / "tracks" / "anime.wav"
RESULTS = Path(__file__).resolve().parent / "results"
SR = 16000


class TimedSource:
    """包住 FileSource，記錄 start() 的時刻作為音訊位置 0 的牆鐘時間。"""

    def __init__(self, inner: FileSource) -> None:
        self.inner = inner
        self.started_at = 0.0

    @property
    def exhausted(self) -> bool:
        return self.inner.exhausted

    def start(self) -> None:
        self.inner.start()
        self.started_at = time.monotonic()

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        return self.inner.read(timeout)

    def stop(self) -> None:
        self.inner.stop()


class PlaceholderTranscriber:
    """ASR 還不能用時的替身：回傳「第 n 次辨識（秒數）」。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.count = 0
        self.lock = threading.Lock()

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        with self.lock:
            self.count += 1
            n = self.count
        return Transcript(
            f"テスト{self.name}{n}（{len(audio) / SR:.1f}秒）", "ja", 1.0, 0.0, -0.1
        )


def ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]：").strip()
    return answer or default


def build(settings: Settings, real_asr: bool) -> PipelineComponents:
    if real_asr:
        print("載入 Silero 與 ASR 模型…", flush=True)
        t = time.monotonic()
        components = factory.build_components(settings, "media")
        print(f"載入完成：{time.monotonic() - t:.1f} s", flush=True)
        return components
    from kikitori.vad import SileroVad

    fast_tr, final_tr = factory.create_translators(settings)
    return PipelineComponents(
        voice_model=SileroVad(),
        fast_transcriber=PlaceholderTranscriber("即時"),
        final_transcriber=PlaceholderTranscriber("完整"),
        fast_translator=fast_tr,
        final_translator=final_tr,
        transcript_filter=default_transcript_filter(0.6),
    )


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p90": round(p90, 3),
        "max": round(max(values), 3),
    }


def run(real_asr: bool) -> None:
    track = Path(ask("音軌", str(DEFAULT_TRACK)))
    if not track.is_file():
        print(f"找不到音軌：{track}")
        return
    defaults = Settings()
    fast_model = ask("即時 ASR 模型", defaults.asr.fast_model) if real_asr else "-"
    final_model = ask("完整 ASR 模型", defaults.asr.final_model) if real_asr else "-"
    device = ask("ASR 裝置（auto／cpu／cuda）", "auto") if real_asr else "auto"
    translate_model = ask("翻譯模型", defaults.translation.final_model)
    patch: dict[str, Any] = {
        "translation": {"fast_model": translate_model, "final_model": translate_model}
    }
    if real_asr:
        patch["asr"] = {
            "fast_model": fast_model,
            "final_model": final_model,
            "device": device,
        }
    settings = defaults.merged(patch)

    components = build(settings, real_asr)
    source = TimedSource(FileSource(track, realtime=True))
    records: list[tuple[float, SubtitleUpdate]] = []
    statuses: list[tuple[str, str | None]] = []

    def on_subtitle(update: SubtitleUpdate) -> None:
        now = time.monotonic()
        records.append((now, update))
        pos = now - source.started_at
        kind = "完整" if update.is_final else "即時"
        print(
            f"[{pos:6.2f}s] #{update.utterance_id} {kind} "
            f"{update.original} → {update.translation}",
            flush=True,
        )

    pipeline = Pipeline(
        components,
        factory.pipeline_config(settings, "media"),
        on_subtitle=on_subtitle,
        on_status=lambda s, d: statuses.append((s, d)),
        on_log=lambda level, m: print(f"  ({level}) {m}", flush=True),
    )
    print(f"開始播放 {track.name}（即時速度）…", flush=True)
    pipeline.start(source)
    pipeline.wait()
    print(f"狀態：{statuses}；被取代而略過的即時工作：{pipeline.dropped_partials}")

    t0 = source.started_at
    partial_lat: list[float] = []
    first_partial: list[float] = []
    final_lat: list[float] = []
    seen_first: set[int] = set()
    for at, u in records:
        if u.is_final:
            final_lat.append(at - (t0 + u.end_sample / SR))
        else:
            partial_lat.append(at - (t0 + u.end_sample / SR))
            if u.utterance_id not in seen_first:
                seen_first.add(u.utterance_id)
                first_partial.append(at - (t0 + u.start_sample / SR))
    summary = {
        "track": track.name,
        "real_asr": real_asr,
        "asr": asdict(settings.asr),
        "translation_model": translate_model,
        "utterances": len([1 for _, u in records if u.is_final]),
        "partial_updates": len(partial_lat),
        "dropped_partials": pipeline.dropped_partials,
        "first_partial_from_start_s": summarize(first_partial),
        "partial_latency_s": summarize(partial_lat),
        "final_latency_from_speech_end_s": summarize(final_lat),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(
        json.dumps(
            {
                "summary": summary,
                "updates": [
                    {"at_s": round(at - t0, 3), **asdict(u)} for at, u in records
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"詳細結果（含台詞，已被 git 忽略）：{out}")


MENU = """
pipeline 端到端實測
  1) 真 ASR（依設定的模型）＋ Ollama 翻譯
  2) 假 ASR（固定文字）＋ Ollama 翻譯
  3) 離開
"""


def main() -> int:
    while True:
        print(MENU)
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice == "1":
            run(real_asr=True)
        elif choice == "2":
            run(real_asr=False)
        elif choice == "3":
            return 0
        else:
            print(f"無效的選項：{choice!r}")


if __name__ == "__main__":
    sys.exit(main())
