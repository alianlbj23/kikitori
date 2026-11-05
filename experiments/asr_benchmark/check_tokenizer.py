"""把 kikitori 自寫的 Whisper 分詞器與 Hugging Face `tokenizers` 逐一比對。

角色：暫時性實驗（不屬於正式程式碼）。`tokenizers` 不是專案依賴（它會帶入
MPL-2.0 的 tqdm），所以由 `menu.py` 以隔離環境執行：

    uv run --isolated --no-project --with tokenizers==0.23.3 --with numpy==2.5.4 \
        python -I check_tokenizer.py <src 目錄> <tokenizer.json>...

`<src 目錄>` 是本專案的 `src/`，讓隔離環境也能匯入 `kikitori.asr.tokenizer`。
"""

import random
import sys
from pathlib import Path

FIXED_SAMPLES = [
    " うん。アイオイキンセーさん",
    "あなた、キンセーさんのご親戚？",
    " トーサン？トーサンって誰？っていうか、君は？",
    "Hello world",
    " Hello, world! It's a test. I'm  here,   you'll see.\n\nNew line\tTab",
    "don't DON'T 'S ''s",
    "數字 123 ４５６ ①② Ⅷ",
    "字幕由Amara.org社区提供",
    "  leading and trailing  ",
    "émoji 😀 ok",
    "a—b–c … «quote»",
    "混合 mixed 日本語 English 中文。",
    "x" * 50,
    " ♪♪♪ ( music )",
]
ALPHABET = "abcXYZ 01 9あいうアイウ漢字。、！？'\"-\n\t  ,.é😀"


def main() -> int:
    """比對所有樣本；有任何不一致就回傳 1。"""
    import tokenizers

    sys.path.insert(0, sys.argv[1])
    from kikitori.asr.tokenizer import WhisperTokenizer

    rng = random.Random(0)
    samples = FIXED_SAMPLES + [
        "".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 40)))
        for _ in range(500)
    ]
    failures = 0
    for path in sys.argv[2:]:
        reference = tokenizers.Tokenizer.from_file(path)
        ours = WhisperTokenizer.from_file(Path(path))
        mismatches = 0
        for text in samples:
            expected = reference.encode(text, add_special_tokens=False).ids
            actual = ours.encode(text)
            if expected != actual or ours.decode(actual) != reference.decode(expected):
                mismatches += 1
                if mismatches <= 5:
                    print(f"MISMATCH {text!r}: {expected} != {actual}")
        print(f"{path}: {len(samples)} samples, {mismatches} mismatches")
        failures += mismatches
    return 1 if failures else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
