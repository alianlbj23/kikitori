"""日語多人說話：能不能分出誰在說話（評測，互動選單）。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集。問題：VTuber 連動有 2–4 人輪流說話，
字幕能不能標出「誰說的」？比較兩種不需要 PyTorch 的做法：

- A 逐句：kikitori 自己的切句器（Silero VAD＋PauseEndpoint，與 app 相同）切出每一句，每句算一個
  說話者向量（ONNX），線上分群（與已知說話者的中心比餘弦相似度，低於門檻就當新的人）。這是能直接
  接進 pipeline 的做法：每句字幕一個標籤，即時。
- B 整段：sherpa-onnx 的離線 diarization（pyannote segmentation-3.0 切段＋說話者向量＋分群）。
  看得到整段音訊、能在一句之中換人，當作上限參考；不是即時的。

測試音訊：YODAS 日語 YouTube 片段（`experiments/vtuber_eval/data/clips/yodas/`，CC BY）。同一支影片的
片段當成同一個人、不同影片當成不同人（假設：一支影片一個主要說話者；有來賓的影片會讓真值變髒），
從 2／3／4 支影片各取 6 句交錯排列，句間 0.3–1.0 秒；另外混入 MUSAN 無人聲音樂（SNR 10／5 dB）
模擬 BGM。說話者向量模型（sherpa-onnx 轉好的 ONNX）見 README。

用法：
  uv run --with sherpa-onnx==2.0.0 python -I -X utf8 experiments/speaker_diarization/menu.py
  uv run --with sherpa-onnx==2.0.0 python -I -X utf8 experiments/speaker_diarization/menu.py --batch all
"""

from __future__ import annotations

import itertools
import json
import random
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "data"
MODELS = DATA / "models"
SCENES = DATA / "scenes"
RESULTS = HERE / "results"
YODAS = ROOT / "experiments" / "vtuber_eval" / "data" / "clips" / "yodas"
SR = 16_000
SEED = 20261109
SPEAKER_COUNTS = (2, 3, 4)
SCENES_PER_COUNT = 8
CLIPS_PER_SPEAKER = 6
CONDITIONS = ("clean", "music10", "music5")
EMBEDDING_MODELS = {
    "campplus-zh-en": "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
    "eres2netv2-zh": "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
    "wespeaker-r34-en": "wespeaker_en_voxceleb_resnet34_LM.onnx",
    "titanet-small-en": "nemo_en_titanet_small.onnx",
}
SEGMENTATION = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
ONLINE_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)
OFFLINE_THRESHOLDS = (0.5, 0.7, 0.9, 1.1)
SHORT_SECS = 1.0


# --------------------------------------------------------------------------- audio


def read_wav(path: Path) -> npt.NDArray[np.int16]:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def write_wav(path: Path, audio: npt.NDArray[np.int16]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(audio.tobytes())


# --------------------------------------------------------------------------- scenes


def clips_by_video() -> dict[str, list[Path]]:
    videos: dict[str, list[Path]] = {}
    for path in sorted(YODAS.glob("*.wav")):
        secs = path.stat().st_size / 2 / SR
        if 1.5 <= secs <= 9.0:  # 太短的向量不可靠、太長的會被句長上限切開
            videos.setdefault(path.stem.split("-")[0], []).append(path)
    return {v: c for v, c in videos.items() if len(c) >= CLIPS_PER_SPEAKER}


def build_scenes() -> list[dict[str, Any]]:
    """依 SEED 產生全部場景（音訊與真值），已存在就重用。"""
    sys.path.insert(0, str(ROOT / "experiments" / "vtuber_eval"))
    from build_sets import mix_at_snr, music_files  # 與 vtuber_eval 相同的混音

    index = SCENES / "scenes.json"
    if index.exists():
        return json.loads(index.read_text(encoding="utf-8"))
    rng = random.Random(SEED)
    videos = clips_by_video()
    music = music_files()
    scenes: list[dict[str, Any]] = []
    for count in SPEAKER_COUNTS:
        for k in range(SCENES_PER_COUNT):
            chosen = rng.sample(sorted(videos), count)
            turns = [
                (speaker, clip)
                for speaker, video in enumerate(chosen)
                for clip in rng.sample(videos[video], CLIPS_PER_SPEAKER)
            ]
            rng.shuffle(turns)
            parts: list[npt.NDArray[np.int16]] = [np.zeros(SR // 2, dtype=np.int16)]
            truth: list[tuple[int, int, int]] = []
            position = SR // 2
            for speaker, clip in turns:
                audio = read_wav(clip)
                truth.append((position, position + len(audio), speaker))
                gap = np.zeros(int(SR * rng.uniform(0.3, 1.0)), dtype=np.int16)
                parts += [audio, gap]
                position += len(audio) + len(gap)
            speech = np.concatenate(parts)
            name = f"n{count}-{k:02d}"
            write_wav(SCENES / f"{name}-clean.wav", speech)
            active = (
                np.concatenate([speech[a:b] for a, b, _ in truth]).astype(np.float32)
                / 32768.0
            )
            power = float(np.mean(active**2))
            track = music[rng.randrange(len(music))]
            bed = read_wav(track).astype(np.float32) / 32768.0
            reps = int(np.ceil(len(speech) / len(bed)))
            bed = np.tile(bed, reps)[: len(speech)]
            for snr in (10, 5):
                mixed, _ = mix_at_snr(speech, bed, power, snr)
                write_wav(SCENES / f"{name}-music{snr}.wav", mixed)
            scenes.append(
                {
                    "name": name,
                    "speakers": count,
                    "videos": chosen,
                    "music": track.name,
                    "truth": truth,
                }
            )
            print(f"scene {name}: {len(turns)} turns, {position / SR:.0f} s")
    index.write_text(json.dumps(scenes, indent=1), encoding="utf-8")
    return scenes


# --------------------------------------------------------------------------- scoring


def best_mapping(pairs: list[tuple[int, int]]) -> dict[int, int]:
    """預測的群 → 真實說話者，讓配對的次數（或樣本數）最大（一對一）。"""
    counts: dict[tuple[int, int], int] = {}
    for pred, true in pairs:
        counts[(pred, true)] = counts.get((pred, true), 0) + 1
    preds = sorted({p for p, _ in pairs})
    trues = sorted({t for _, t in pairs})
    best: dict[int, int] = {}
    best_score = -1
    if len(preds) >= len(trues):
        for chosen in itertools.permutations(preds, len(trues)):
            score = sum(
                counts.get((p, t), 0) for p, t in zip(chosen, trues, strict=True)
            )
            if score > best_score:
                best_score, best = score, dict(zip(chosen, trues, strict=True))
    else:
        for chosen in itertools.permutations(trues, len(preds)):
            score = sum(
                counts.get((p, t), 0) for p, t in zip(preds, chosen, strict=True)
            )
            if score > best_score:
                best_score, best = score, dict(zip(preds, chosen, strict=True))
    return best


def truth_of(start: int, end: int, truth: list[list[int]]) -> int | None:
    """這一段主要是誰（重疊超過一半才算），否則 None。"""
    overlaps: dict[int, int] = {}
    for a, b, speaker in truth:
        o = min(end, b) - max(start, a)
        if o > 0:
            overlaps[speaker] = overlaps.get(speaker, 0) + o
    if not overlaps:
        return None
    speaker, o = max(overlaps.items(), key=lambda kv: kv[1])
    return speaker if o >= 0.5 * (end - start) else None


# --------------------------------------------------------------------------- method A


@dataclass
class Utterance:
    start: int
    end: int
    speaker: int | None
    embedding: npt.NDArray[np.float32]


def vad_utterances(audio: npt.NDArray[np.int16]) -> list[tuple[int, int]]:
    from kikitori.vad import PauseEndpoint, Segmenter, SileroVad, UtteranceEnded

    segmenter = Segmenter(SileroVad(), endpoint=PauseEndpoint(0.6, 10.0))
    events = segmenter.process(audio) + segmenter.flush()
    return [
        (e.start_sample, e.end_sample) for e in events if isinstance(e, UtteranceEnded)
    ]


def embed(extractor: Any, audio: npt.NDArray[np.int16]) -> npt.NDArray[np.float32]:
    stream = extractor.create_stream()
    stream.accept_waveform(SR, audio.astype(np.float32) / 32768.0)
    stream.input_finished()
    vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    return vector / max(float(np.linalg.norm(vector)), 1e-9)


def online_cluster(
    embeddings: list[npt.NDArray[np.float32]], threshold: float, max_speakers: int = 8
) -> list[int]:
    """依序指派：與最像的已知說話者中心相似度 ≥ 門檻就歸它，否則開新的一人（最多 `max_speakers`）。"""
    centers: list[npt.NDArray[np.float32]] = []
    counts: list[int] = []
    labels: list[int] = []
    for e in embeddings:
        sims = [float(c @ e) / max(float(np.linalg.norm(c)), 1e-9) for c in centers]
        best = int(np.argmax(sims)) if sims else -1
        if best >= 0 and (sims[best] >= threshold or len(centers) >= max_speakers):
            centers[best] = centers[best] + e
            counts[best] += 1
            labels.append(best)
        else:
            centers.append(e.copy())
            counts.append(1)
            labels.append(len(centers) - 1)
    return labels


def run_online(scenes: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    import sherpa_onnx

    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(MODELS / EMBEDDING_MODELS[model]), num_threads=4
        )
    )
    rows = []
    for scene in scenes:
        for condition in CONDITIONS:
            audio = read_wav(SCENES / f"{scene['name']}-{condition}.wav")
            started = time.perf_counter()
            utts = [
                Utterance(
                    a, b, truth_of(a, b, scene["truth"]), embed(extractor, audio[a:b])
                )
                for a, b in vad_utterances(audio)
            ]
            secs_per_utt = (time.perf_counter() - started) / max(len(utts), 1)
            for threshold in ONLINE_THRESHOLDS:
                labels = online_cluster([u.embedding for u in utts], threshold)
                scored = [
                    (lab, u.speaker)
                    for lab, u in zip(labels, utts, strict=True)
                    if u.speaker is not None
                ]
                mapping = best_mapping(scored)
                correct = [mapping.get(p) == t for p, t in scored]
                short = [
                    mapping.get(lab) == u.speaker
                    for lab, u in zip(labels, utts, strict=True)
                    if u.speaker is not None and (u.end - u.start) / SR < SHORT_SECS
                ]
                rows.append(
                    {
                        "method": "online",
                        "model": model,
                        "scene": scene["name"],
                        "speakers": scene["speakers"],
                        "condition": condition,
                        "threshold": threshold,
                        "utterances": len(utts),
                        "accuracy": sum(correct) / max(len(correct), 1),
                        "short_accuracy": (sum(short) / len(short)) if short else None,
                        "found": len(set(labels)),
                        "secs_per_utterance": round(secs_per_utt, 4),
                    }
                )
        print(f"  online {model} {scene['name']} done")
    return rows


# --------------------------------------------------------------------------- method B


def frame_labels(
    segments: list[tuple[int, int, int]], length: int, hop: int = 160
) -> npt.NDArray[np.int32]:
    """每 10 ms 一格的說話者（-1 = 沒人說話；重疊時取後到的那段）。"""
    frames = np.full(length // hop + 1, -1, dtype=np.int32)
    for a, b, speaker in segments:
        frames[a // hop : b // hop] = speaker
    return frames


def der(
    truth: list[list[int]], pred: list[tuple[int, int, int]], length: int
) -> dict[str, float]:
    ref = frame_labels([tuple(t) for t in truth], length)
    hyp = frame_labels(pred, length)
    speech = ref >= 0
    pairs = [
        (int(h), int(r)) for h, r in zip(hyp, ref, strict=True) if h >= 0 and r >= 0
    ]
    mapping = best_mapping(pairs) if pairs else {}
    mapped = np.array([mapping.get(int(h), -2) if h >= 0 else -1 for h in hyp])
    miss = float(np.sum(speech & (hyp < 0)))
    false_alarm = float(np.sum(~speech & (hyp >= 0)))
    confusion = float(np.sum(speech & (hyp >= 0) & (mapped != ref)))
    total = max(float(np.sum(speech)), 1.0)
    return {
        "der": (miss + false_alarm + confusion) / total,
        "miss": miss / total,
        "fa": false_alarm / total,
        "confusion": confusion / total,
    }


def run_offline(scenes: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    import sherpa_onnx

    rows = []
    for threshold in OFFLINE_THRESHOLDS:
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(SEGMENTATION)
                ),
                num_threads=4,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(MODELS / EMBEDDING_MODELS[model]), num_threads=4
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1, threshold=threshold
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        for scene in scenes:
            for condition in CONDITIONS:
                audio = read_wav(SCENES / f"{scene['name']}-{condition}.wav")
                started = time.perf_counter()
                result = diarizer.process(
                    audio.astype(np.float32) / 32768.0
                ).sort_by_start_time()
                elapsed = time.perf_counter() - started
                pred = [
                    (int(s.start * SR), int(s.end * SR), int(s.speaker)) for s in result
                ]
                rows.append(
                    {
                        "method": "offline",
                        "model": model,
                        "scene": scene["name"],
                        "speakers": scene["speakers"],
                        "condition": condition,
                        "threshold": threshold,
                        "found": len({p[2] for p in pred}),
                        "rtf": round(elapsed / (len(audio) / SR), 4),
                        **der(scene["truth"], pred, len(audio)),
                    }
                )
        print(f"  offline {model} threshold {threshold} done")
    return rows


# --------------------------------------------------------------------------- reporting


def summarize(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    online = [r for r in rows if r["method"] == "online"]
    offline = [r for r in rows if r["method"] == "offline"]
    if online:
        out += ["## A 逐句（kikitori 切句＋線上分群）：句子正確率（中位數）", ""]
        out += [
            "| 模型 | 門檻 | 2 人 乾淨 | 2 人 BGM10 | 3 人 乾淨 | 3 人 BGM10 | 4 人 乾淨 | 4 人 BGM10 | 4 人 BGM5 | 找到人數−真實（中位數） | 短句(<1s)正確率 |",
            "|" + "---|" * 11,
        ]
        for (model, threshold), group in itertools.groupby(
            sorted(online, key=lambda r: (r["model"], r["threshold"])),
            key=lambda r: (r["model"], r["threshold"]),
        ):
            g = list(group)

            def acc(n: int, cond: str, g: list[dict[str, Any]] = g) -> str:
                v = [
                    r["accuracy"]
                    for r in g
                    if r["speakers"] == n and r["condition"] == cond
                ]
                return f"{statistics.median(v):.2f}" if v else "-"

            extra = statistics.median([r["found"] - r["speakers"] for r in g])
            short = [r["short_accuracy"] for r in g if r["short_accuracy"] is not None]
            short_text = f"{statistics.median(short):.2f}" if short else "-"
            out.append(
                f"| {model} | {threshold} | {acc(2, 'clean')} | {acc(2, 'music10')} | {acc(3, 'clean')} | {acc(3, 'music10')} | "
                f"{acc(4, 'clean')} | {acc(4, 'music10')} | {acc(4, 'music5')} | {extra:+.0f} | {short_text} |"
            )
        out.append("")
    if offline:
        out += [
            "## B 整段（sherpa-onnx：pyannote segmentation-3.0＋向量＋分群）：DER（中位數，越低越好）",
            "",
        ]
        out += [
            "| 模型 | 門檻 | 2 人 乾淨 | 2 人 BGM10 | 3 人 乾淨 | 4 人 乾淨 | 4 人 BGM10 | 4 人 BGM5 | 其中說話者錯（4 人 乾淨） | 找到人數−真實 | RTF |",
            "|" + "---|" * 11,
        ]
        for (model, threshold), group in itertools.groupby(
            sorted(offline, key=lambda r: (r["model"], r["threshold"])),
            key=lambda r: (r["model"], r["threshold"]),
        ):
            g = list(group)

            def d(
                n: int, cond: str, key: str = "der", g: list[dict[str, Any]] = g
            ) -> str:
                v = [r[key] for r in g if r["speakers"] == n and r["condition"] == cond]
                return f"{statistics.median(v):.2f}" if v else "-"

            extra = statistics.median([r["found"] - r["speakers"] for r in g])
            out.append(
                f"| {model} | {threshold} | {d(2, 'clean')} | {d(2, 'music10')} | {d(3, 'clean')} | {d(4, 'clean')} | {d(4, 'music10')} | "
                f"{d(4, 'music5')} | {d(4, 'clean', 'confusion')} | {extra:+.0f} | {statistics.median(r['rtf'] for r in g):.3f} |"
            )
    return out


def run(models: list[str], methods: list[str]) -> None:
    scenes = build_scenes()
    RESULTS.mkdir(exist_ok=True)
    for model in models:
        rows: list[dict[str, Any]] = []
        if "online" in methods:
            rows += run_online(scenes, model)
        if "offline" in methods:
            rows += run_offline(scenes, model)
        path = RESULTS / f"{model}-{'+'.join(methods)}.json"
        path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"saved {path}")
        print("\n".join(summarize(rows)))


def show_saved() -> None:
    rows: list[dict[str, Any]] = []
    for path in sorted(RESULTS.glob("*.json")):
        rows += json.loads(path.read_text(encoding="utf-8"))
    print("\n".join(summarize(rows)))


def choose(prompt: str, options: list[str]) -> int | None:
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    answer = input(prompt).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return int(answer) - 1
    return None


def main() -> None:
    if "--batch" in sys.argv:
        what = (
            sys.argv[sys.argv.index("--batch") + 1]
            if len(sys.argv) > sys.argv.index("--batch") + 1
            else "all"
        )
        if what == "summary":
            show_saved()
        elif what == "scenes":
            build_scenes()
        else:
            run(list(EMBEDDING_MODELS), ["online", "offline"])
        return
    while True:
        print("\n日語多人說話：說話者分群評測")
        pick = choose(
            "選擇（Enter 結束）：",
            [
                "產生測試場景",
                "全部模型兩種做法",
                "只跑 A 逐句",
                "只跑 B 整段",
                "顯示已存的摘要",
            ],
        )
        if pick is None:
            return
        if pick == 0:
            build_scenes()
        elif pick in (1, 2, 3):
            methods = {1: ["online", "offline"], 2: ["online"], 3: ["offline"]}[pick]
            m = choose("模型（Enter = 全部）：", list(EMBEDDING_MODELS))
            run(
                list(EMBEDDING_MODELS) if m is None else [list(EMBEDDING_MODELS)[m]],
                methods,
            )
        else:
            show_saved()


if __name__ == "__main__":
    main()
