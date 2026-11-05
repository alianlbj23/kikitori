"""Silero 重置策略評測：產生評測音軌、跑三種重置策略與前文對照、輸出指標表。

角色：`menu.py` 的後端（暫時性實驗，不屬於正式程式碼）。用正式的
`kikitori.vad`（`SileroVad` + `Segmenter`）跑每條音軌，以
`kikitori.evaluation` 的混音與指標計算結果，用來決定 `ResetPolicy` 的預設值。

人聲來源沿用 `experiments/pipecat_vad/data/tracks/`（本機、git 忽略）。除了原本
的版面，另外把同一批片段重新排成「長間隔」版面（句間 3–4 秒）：原版面的
間隔最長只有 1.5 秒，(c) 的 2 秒非人聲重置根本不會發生，無法比較。
"""

import json
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.evaluation import metrics, mixing, noise, wav
from kikitori.vad import (
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    ResetPolicy,
    Segmenter,
    SileroVad,
    UtteranceEnded,
    UtteranceStarted,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_SOURCE_DIR = REPO / "experiments" / "pipecat_vad" / "data" / "tracks"
DEFAULT_DATA_DIR = HERE / "data"

THRESHOLD = 0.5
GAP_FILL_SNR_DB = 10.0
OVERLAY_SNR_DB = (20.0, 10.0, 5.0)
NOISE_SEED = 20261105
LONG_LEAD_IN_SECS = 3.0
# 保留 0.15 / 0.5 秒的短間隔（測試合併成一句），其餘拉長到超過 (c) 的 2 秒。
LONG_GAPS_SECS = (3.0, 0.15, 3.0, 0.5, 4.0, 3.0)
MUSAN_PIECE_SAMPLES = 8 * SAMPLE_RATE
MUSAN_OFFSET_SAMPLES = 30 * SAMPLE_RATE
LOW_PROBABILITY_RATIO = 0.5  # 沒接前文時人聲區平均機率 < 正常的一半 → 異常偏低

Log = Callable[[str], None]


@dataclass(frozen=True)
class Paths:
    """評測讀寫的位置；測試時指向暫存資料夾。"""

    source_dir: Path = DEFAULT_SOURCE_DIR
    data_dir: Path = DEFAULT_DATA_DIR

    @property
    def tracks_dir(self) -> Path:
        return self.data_dir / "tracks"

    @property
    def musan_dir(self) -> Path:
        return self.data_dir / "musan"

    @property
    def results_md(self) -> Path:
        return self.data_dir / "results.md"

    @property
    def results_json(self) -> Path:
        return self.data_dir / "results.json"


@dataclass(frozen=True)
class RunConfig:
    """一組評測設定：重置策略 + 前文長度。"""

    name: str
    policy: ResetPolicy
    context_samples: int = 64


RUN_CONFIGS = (
    RunConfig("a_stream_start", ResetPolicy.STREAM_START),
    RunConfig("b_after_utterance", ResetPolicy.AFTER_UTTERANCE),
    RunConfig("c_after_long_non_speech", ResetPolicy.AFTER_LONG_NON_SPEECH),
    RunConfig("a_no_context", ResetPolicy.STREAM_START, context_samples=0),
)


@dataclass
class RunResult:
    """一條音軌在一組設定下的指標。NaN 表示該指標無定義（例如沒有非人聲）。"""

    track: str
    config: str
    segments: int
    utterances: int
    resets: int
    auc: float
    false_alarm: float
    miss: float
    onset_median_ms: float
    onset_p90_ms: float
    onset_count: int
    premature_cuts: int
    missed_segments: int
    mean_prob_speech: float
    mean_prob_non_speech: float
    ms_per_window: float
    detections: list[tuple[int, int, int]] = field(default_factory=list)


class RecordingModel:
    """包住真模型，記錄每個視窗的機率、推論時間與 reset 次數。"""

    def __init__(self, inner: SileroVad) -> None:
        self.inner = inner
        self.probs: list[float] = []
        self.seconds = 0.0
        self.resets = 0

    def probability(self, window: npt.NDArray[np.int16]) -> float:
        start = time.perf_counter()
        prob = self.inner.probability(window)
        self.seconds += time.perf_counter() - start
        self.probs.append(prob)
        return prob

    def reset(self) -> None:
        self.resets += 1
        self.inner.reset()


# ---------- 產生評測音軌 ----------


def load_source_truth(source_dir: Path) -> dict[str, Any]:
    """讀取來源音軌的 `ground_truth.json`。

    Raises:
        FileNotFoundError: 本機還沒有來源音軌。
        ValueError: JSON 格式不符。
    """
    path = source_dir / "ground_truth.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"找不到 {path}。請先依 experiments/pipecat_vad/README.md 執行 "
            "fetch_samples.py 與 build_tracks.py 產生動漫、VTuber 音軌。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    valid = (
        isinstance(data, dict)
        and data.get("sample_rate") == SAMPLE_RATE
        and isinstance(data.get("tracks"), dict)
    )
    if not valid:
        raise ValueError(
            f"{path} must be an object with sample_rate {SAMPLE_RATE} and a 'tracks' object"
        )
    return data


def speech_sources(
    paths: Paths, log: Log
) -> dict[str, tuple[np.ndarray, dict[str, Any]]]:
    """回傳有人聲區間的來源音軌，以及由同一批片段重排的長間隔版面。"""
    truth = load_source_truth(paths.source_dir)
    sources: dict[str, tuple[np.ndarray, dict[str, Any]]] = {}
    for name, track_truth in truth["tracks"].items():
        if not track_truth.get("segments"):
            continue  # 例如 synthetic：沒有人聲，混音無從計算 SNR
        audio = wav.read_wav(paths.source_dir / f"{name}.wav")
        segments = mixing.segments_from_truth(track_truth)
        if len(audio) != track_truth["samples"]:
            raise ValueError(
                f"{name}.wav length {len(audio)} != truth {track_truth['samples']}"
            )
        sources[name] = (audio, track_truth)
        sources[f"{name}_longgap"] = relayout(audio, track_truth, segments)
        log(f"  來源 {name}：{len(segments)} 段人聲，{len(audio) / SAMPLE_RATE:.1f} 秒")
    if not sources:
        raise ValueError(f"{paths.source_dir} has no track with speech segments")
    return sources


def relayout(
    audio: np.ndarray, track_truth: Mapping[str, Any], segments: list[tuple[int, int]]
) -> tuple[np.ndarray, dict[str, Any]]:
    """把人聲片段以 `LONG_GAPS_SECS` 的間隔重新排列（間隔為數位靜音）。"""
    parts = [np.zeros(round(LONG_LEAD_IN_SECS * SAMPLE_RATE), dtype=np.int16)]
    cursor = len(parts[0])
    new_segments = []
    for i, ((start, end), meta) in enumerate(
        zip(segments, track_truth["segments"], strict=True)
    ):
        gap_secs = LONG_GAPS_SECS[i % len(LONG_GAPS_SECS)]
        clip = audio[start:end]
        new_segments.append(
            {
                "file": meta.get("file", f"segment_{i}"),
                "start_sample": cursor,
                "end_sample": cursor + len(clip),
                "gap_after_secs": gap_secs,
            }
        )
        gap = np.zeros(round(gap_secs * SAMPLE_RATE), dtype=np.int16)
        parts += [clip, gap]
        cursor += len(clip) + len(gap)
    out = np.concatenate(parts)
    return out, {"samples": len(out), "segments": new_segments}


def background_sources(
    paths: Paths, length: int, log: Log
) -> dict[str, tuple[np.ndarray, list[str]]]:
    """可用的背景素材：白／粉紅噪音一定有；MUSAN 下載好時加上音樂與雜音。

    MUSAN 音樂依 `ANNOTATIONS` 的 vocals 欄分成「有人聲演唱」與「純音樂」兩種：
    官方承認歌聲是 Silero 的弱點，兩者的誤報與漏抓行為不同，混在一起會互相掩蓋。
    """
    backgrounds = {
        "white": (noise.white_noise(length, -30.0, NOISE_SEED), ["generated"]),
        "pink": (noise.pink_noise(length, -30.0, NOISE_SEED), ["generated"]),
    }
    musan = _load_musan_module()
    if not musan.is_ready(paths.musan_dir):
        log("  MUSAN 尚未下載：只產生白噪音、粉紅噪音版本（可在選單先下載 MUSAN）。")
        return backgrounds
    groups = {
        **music_groups(paths.musan_dir / "music"),
        "noise": _wav_files(paths.musan_dir / "noise"),
    }
    for name, files in groups.items():
        if not files:
            log(f"  MUSAN 沒有 {name} 類的檔案，略過這個背景。")
            continue
        backgrounds[f"musan_{name}"] = musan_background(
            files, paths.musan_dir, length, NOISE_SEED, log
        )
    return backgrounds


def music_groups(music_dir: Path) -> dict[str, list[Path]]:
    """依 MUSAN 音樂各子資料夾的 `ANNOTATIONS`（檔名 曲風 vocals 作者）分組。

    vocals 為 Y 的歸入 `music_vocal`；N 或沒有標註的（例如古典樂資料夾）歸入
    `music_inst`。
    """
    vocal: set[str] = set()
    for annotations in music_dir.rglob("ANNOTATIONS"):
        for line in annotations.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[2] == "Y":
                vocal.add(fields[0])
    files = _wav_files(music_dir)
    return {
        "music_vocal": [f for f in files if f.stem in vocal],
        "music_inst": [f for f in files if f.stem not in vocal],
    }


def musan_background(
    files: list[Path], root: Path, length: int, seed: int, log: Log
) -> tuple[np.ndarray, list[str]]:
    """以固定種子挑 MUSAN 檔案，各取一段串接到 `length` 樣本；回傳音訊與用到的檔名。

    格式不符（非 16 kHz 單聲道 16-bit）的檔案記錄後跳過，不中斷整個流程。
    """
    if not files:
        raise FileNotFoundError(f"no MUSAN WAV files given under {root}")
    rng = np.random.default_rng(seed)
    pieces: list[np.ndarray] = []
    used: list[str] = []
    total = 0
    for index in rng.permutation(len(files)):
        path = files[index]
        try:
            audio = wav.read_wav(path)
        except ValueError as exc:
            log(f"  略過格式不符的 MUSAN 檔案：{exc}")
            continue
        # 每個檔案只取一段，讓一條背景混到多首曲子／多種聲音；從 30 秒處起取以避開前奏。
        offset = min(MUSAN_OFFSET_SAMPLES, max(0, len(audio) - MUSAN_PIECE_SAMPLES))
        piece = audio[offset : offset + MUSAN_PIECE_SAMPLES]
        if not np.any(piece):
            continue
        pieces.append(piece)
        used.append(path.relative_to(root).as_posix())
        total += len(piece)
        if total >= length:
            break
    if total < length:
        raise ValueError(
            f"MUSAN files under {root} are too short to fill {length} samples"
        )
    return np.concatenate(pieces)[:length], used


def _wav_files(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.wav")) if folder.is_dir() else []


def generate_tracks(paths: Paths, log: Log = print) -> list[str]:
    """產生所有音軌版本與 `ground_truth.json`，回傳產生的音軌名稱。"""
    sources = speech_sources(paths, log)
    longest = max(len(audio) for audio, _ in sources.values())
    backgrounds = background_sources(paths, longest, log)
    paths.tracks_dir.mkdir(parents=True, exist_ok=True)
    truth_out: dict[str, Any] = {"sample_rate": SAMPLE_RATE, "tracks": {}}

    def save(name: str, audio: np.ndarray, track_truth: dict[str, Any]) -> None:
        wav.write_wav(paths.tracks_dir / f"{name}.wav", audio)
        truth_out["tracks"][name] = track_truth

    for source, (audio, track_truth) in sources.items():
        save(f"{source}__original", audio, {**track_truth, "source": source})
        for label, (bg, files) in backgrounds.items():
            gap = mixing.fill_gaps(audio, track_truth, bg, GAP_FILL_SNR_DB, label)
            gap.truth["mix"]["files"] = files
            save(
                f"{source}__gapfill-{label}", gap.audio, {**gap.truth, "source": source}
            )
            for snr in OVERLAY_SNR_DB:
                mix = mixing.overlay(audio, track_truth, bg, snr, label)
                mix.truth["mix"]["files"] = files
                save(
                    f"{source}__overlay-{label}-snr{snr:g}",
                    mix.audio,
                    {**mix.truth, "source": source},
                )
    (paths.tracks_dir / "ground_truth.json").write_text(
        json.dumps(truth_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    names = list(truth_out["tracks"])
    log(f"已產生 {len(names)} 條音軌 → {paths.tracks_dir}")
    return names


# ---------- 執行評測 ----------


def evaluate_track(
    name: str,
    audio: np.ndarray,
    segments: list[tuple[int, int]],
    config: RunConfig,
    vad: SileroVad,
) -> RunResult:
    """用一組設定跑一條音軌，回傳指標。

    逐視窗餵入，才能記下每個 `UtteranceStarted` 送出時的確切串流位置
    （分批大小不影響事件，已由單元測試保證）。
    """
    model = RecordingModel(vad)
    segmenter = Segmenter(model, reset_policy=config.policy)
    detections: list[metrics.Detection] = []
    open_utterance: tuple[int, int] | None = None
    events_at: list[tuple[int, object]] = []
    for offset in range(0, len(audio), WINDOW_SAMPLES):
        chunk = audio[offset : offset + WINDOW_SAMPLES]
        position = offset + len(chunk)
        events_at += [(position, e) for e in segmenter.process(chunk)]
    # 建構時與 flush 的串流重置不算，只算音軌進行中由策略造成的重置。
    policy_resets = model.resets - 1
    events_at += [(len(audio), e) for e in segmenter.flush()]
    for position, event in events_at:
        if isinstance(event, UtteranceStarted):
            open_utterance = (event.start_sample, position)
        elif isinstance(event, UtteranceEnded):
            if open_utterance is None:
                raise RuntimeError(f"{name}: UtteranceEnded without UtteranceStarted")
            detections.append(
                metrics.Detection(
                    open_utterance[0], event.end_sample, open_utterance[1]
                )
            )
            open_utterance = None

    probs = np.asarray(model.probs)
    labels = metrics.frame_labels(len(probs), segments)
    latencies = metrics.onset_latencies_ms(segments, detections)
    median, p90 = metrics.median_and_p90(latencies)
    return RunResult(
        track=name,
        config=config.name,
        segments=len(segments),
        utterances=len(detections),
        resets=policy_resets,
        auc=metrics.roc_auc(probs, labels),
        false_alarm=metrics.false_alarm_rate(probs, labels, THRESHOLD),
        miss=metrics.miss_rate(probs, labels, THRESHOLD),
        onset_median_ms=median,
        onset_p90_ms=p90,
        onset_count=len(latencies),
        premature_cuts=metrics.premature_cuts(segments, detections),
        missed_segments=metrics.missed_segments(segments, detections),
        mean_prob_speech=_mean(probs[labels]),
        mean_prob_non_speech=_mean(probs[~labels]),
        ms_per_window=1000.0 * model.seconds / max(1, len(probs)),
        detections=[(d.start_sample, d.end_sample, d.started_at) for d in detections],
    )


def run_evaluation(paths: Paths, log: Log = print) -> list[RunResult]:
    """對所有已產生的音軌跑全部設定，寫出 results.md 與 results.json。

    Raises:
        FileNotFoundError: 還沒產生評測音軌。
    """
    truth_path = paths.tracks_dir / "ground_truth.json"
    if not truth_path.is_file():
        raise FileNotFoundError(f"找不到 {truth_path}，請先在選單選「產生評測音軌」。")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    vads = {
        ctx: SileroVad(context_samples=ctx)
        for ctx in {c.context_samples for c in RUN_CONFIGS}
    }
    results: list[RunResult] = []
    for name, track_truth in truth["tracks"].items():
        audio = wav.read_wav(paths.tracks_dir / f"{name}.wav")
        segments = mixing.segments_from_truth(track_truth)
        for config in RUN_CONFIGS:
            results.append(
                evaluate_track(
                    name, audio, segments, config, vads[config.context_samples]
                )
            )
        log(f"  {name}：完成")
    report = format_report(results)
    paths.results_md.write_text(report, encoding="utf-8")
    paths.results_json.write_text(
        json.dumps(
            [_nan_to_none(asdict(r)) for r in results],
            ensure_ascii=False,
            indent=1,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    log(report)
    log(f"結果已寫入 {paths.results_md} 與 {paths.results_json}")
    return results


def context_rows(results: Iterable[RunResult]) -> list[tuple[str, float, float, bool]]:
    """前文對照：每條音軌 (a) 正常與沒接前文的人聲區平均機率，以及是否異常偏低。"""
    by_key = {(r.track, r.config): r for r in results}
    rows = []
    for (track, config), normal in by_key.items():
        if config != "a_stream_start" or (track, "a_no_context") not in by_key:
            continue
        no_ctx = by_key[(track, "a_no_context")]
        low = no_ctx.mean_prob_speech < LOW_PROBABILITY_RATIO * normal.mean_prob_speech
        rows.append((track, normal.mean_prob_speech, no_ctx.mean_prob_speech, low))
    return rows


def variant_of(track: str) -> str:
    """`anime_longgap__overlay-pink-snr10` → `overlay-pink-snr10`。"""
    return track.split("__", 1)[1] if "__" in track else track


def layout_of(track: str) -> str:
    return "longgap" if track.split("__", 1)[0].endswith("_longgap") else "original"


def format_report(results: list[RunResult]) -> str:
    """產生 Markdown：彙總表（依版本 × 策略）、明細表、前文對照。"""
    lines = ["# Silero 重置策略評測結果", ""]
    lines += ["## 彙總（同版面、同版本的各來源音軌合計／平均）", ""]
    lines += [
        (
            "| 版面 | 版本 | 設定 | AUC | 誤報率 | 漏報率 | 開始延遲中位數 ms | P90 ms | "
            "提早切斷 | 漏抓／總段數 | 句數 | 重置次數 |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    groups: dict[tuple[str, str, str], list[RunResult]] = {}
    for r in results:
        groups.setdefault(
            (layout_of(r.track), variant_of(r.track), r.config), []
        ).append(r)
    for (layout, variant, config), rows in groups.items():
        latencies_med = [r.onset_median_ms for r in rows]
        lines.append(
            f"| {layout} | {variant} | {config} | {_fmt(_nanmean([r.auc for r in rows]), 3)} | "
            f"{_pct(_nanmean([r.false_alarm for r in rows]))} | "
            f"{_pct(_nanmean([r.miss for r in rows]))} | "
            f"{_fmt(_nanmean(latencies_med), 0)} | {_fmt(_nanmean([r.onset_p90_ms for r in rows]), 0)} | "
            f"{sum(r.premature_cuts for r in rows)} | "
            f"{sum(r.missed_segments for r in rows)}/{sum(r.segments for r in rows)} | "
            f"{sum(r.utterances for r in rows)} | {sum(r.resets for r in rows)} |"
        )
    lines += ["", "## 明細", ""]
    lines += [
        "| 音軌 | 設定 | AUC | 誤報率 | 漏報率 | 延遲中位數 | P90 | 提早切斷 | 漏抓 | 句數 | 重置 | ms/視窗 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.track} | {r.config} | {_fmt(r.auc, 3)} | {_pct(r.false_alarm)} | {_pct(r.miss)} | "
            f"{_fmt(r.onset_median_ms, 0)} | {_fmt(r.onset_p90_ms, 0)} | {r.premature_cuts} | "
            f"{r.missed_segments}/{r.segments} | {r.utterances} | {r.resets} | {r.ms_per_window:.3f} |"
        )
    lines += ["", "## 前文對照（context_samples=0）", ""]
    lines += [
        "| 音軌 | 人聲區平均機率（64 前文） | （無前文） | 判定 |",
        "|---|---|---|---|",
    ]
    for track, normal, no_ctx, low in context_rows(results):
        verdict = "機率異常偏低" if low else "正常"
        lines.append(f"| {track} | {normal:.3f} | {no_ctx:.3f} | {verdict} |")
    timing = [r.ms_per_window for r in results if r.config != "a_no_context"]
    lines += [
        "",
        f"每視窗平均推論時間：{_nanmean(timing):.3f} ms（{len(timing)} 次執行平均）",
        "",
    ]
    return "\n".join(lines)


def _load_musan_module() -> Any:
    # 以檔案路徑載入同資料夾的 musan.py：用 `python -I` 執行時腳本所在資料夾不在 sys.path。
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "silero_reset_musan", HERE / "musan.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / 'musan.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mean(values: np.ndarray) -> float:
    return float(values.mean()) if values.size else math.nan


def _nanmean(values: list[float]) -> float:
    finite = [v for v in values if not math.isnan(v)]
    return sum(finite) / len(finite) if finite else math.nan


def _fmt(value: float, digits: int) -> str:
    return "—" if math.isnan(value) else f"{value:.{digits}f}"


def _pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{100 * value:.1f}%"


def _nan_to_none(row: dict[str, Any]) -> dict[str, Any]:
    # 標準 JSON 沒有 NaN；無定義的指標寫成 null。
    return {
        k: None if isinstance(v, float) and math.isnan(v) else v for k, v in row.items()
    }
