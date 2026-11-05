"""Experiment helper: download a handful of Japanese voice clips from Hugging Face.

Role: test-data source for the pipecat VAD feasibility check. Only a few clips are
fetched (not whole datasets) and they stay in data/raw/, which is git-ignored.
"""

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 30
# Guards against a bad URL streaming something huge; voice clips are well under this.
MAX_CLIP_BYTES = 5 * 1024 * 1024
RAW_DIR = Path(__file__).resolve().parent / "data" / "raw"

ANIME_DATASET = "joujiboi/japanese-anime-speech"
VTUBER_DATASET = "Elite35P-Server/EliteVoiceProject"
VTUBER_DIR = "audio_raw/youtube/test"


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def download(url: str, dest: Path) -> int:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
        data = resp.read(MAX_CLIP_BYTES + 1)
    if len(data) > MAX_CLIP_BYTES:
        raise ValueError(f"{url} exceeds {MAX_CLIP_BYTES} bytes")
    dest.write_bytes(data)
    return len(data)


def fetch_anime(count: int) -> list[dict[str, Any]]:
    # The datasets-server rows API hands out per-clip URLs, so we avoid pulling
    # multi-GB parquet shards just to get a few seconds of audio.
    query = urllib.parse.urlencode(
        {
            "dataset": ANIME_DATASET,
            "config": "default",
            "split": "train",
            "offset": 0,
            "length": 40,
        }
    )
    rows = get_json(f"https://datasets-server.huggingface.co/rows?{query}")["rows"]
    picked: list[dict[str, Any]] = []
    for item in rows:
        row = item["row"]
        text = row.get("transcription")
        audio = row.get("audio")
        # Prefer full sentences of moderate length: long enough to exercise
        # STARTING -> SPEAKING, short enough to keep the test tracks small.
        if not isinstance(text, str) or not (12 <= len(text) <= 40):
            continue
        if not isinstance(audio, list) or not audio or "src" not in audio[0]:
            continue
        dest = RAW_DIR / f"anime_{item['row_idx']:03d}.mp3"
        size = download(audio[0]["src"], dest)
        picked.append(
            {"file": dest.name, "source": ANIME_DATASET, "text": text, "bytes": size}
        )
        if len(picked) == count:
            break
    return picked


def fetch_vtuber(count: int) -> list[dict[str, Any]]:
    tree_url = (
        f"https://huggingface.co/api/datasets/{VTUBER_DATASET}/tree/main/"
        f"{VTUBER_DIR}?recursive=1"
    )
    entries = get_json(tree_url)
    mp3s = sorted(e["path"] for e in entries if e.get("path", "").endswith(".mp3"))
    picked: list[dict[str, Any]] = []
    for path in mp3s[:count]:
        url = (
            f"https://huggingface.co/datasets/{VTUBER_DATASET}/resolve/main/"
            f"{urllib.parse.quote(path)}"
        )
        dest = RAW_DIR / f"vtuber_{len(picked):03d}.mp3"
        size = download(url, dest)
        picked.append(
            {"file": dest.name, "source": f"{VTUBER_DATASET}/{path}", "bytes": size}
        )
    return picked


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"anime": fetch_anime(6), "vtuber": fetch_vtuber(6)}
    (RAW_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for group, clips in manifest.items():
        total = sum(c["bytes"] for c in clips)
        print(f"{group}: {len(clips)} clips, {total / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
