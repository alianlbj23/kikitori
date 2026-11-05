"""可選的 ASR 模型清單：每個模型固定 Hugging Face commit 與每個檔案的 SHA-256。

位於 asr 模組的設定層：下載器依這裡的資料取得並驗證模型，`hardware` 模組與
設定頁也從這裡列出可選模型。固定 revision 與雜湊，確保使用者拿到的檔案
與基準測試量過的完全相同，也防止上游被改動。
"""

from dataclasses import dataclass
from typing import Literal

SpeedTier = Literal["fastest", "fast", "medium", "slow", "slowest"]

_MULTILINGUAL: tuple[str, ...] = ("multilingual",)


@dataclass(frozen=True)
class ModelFile:
    """模型目錄中的一個檔案。"""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    """一個 CTranslate2 格式的 Whisper 模型。

    Attributes:
        id: 設定檔與引擎協定使用的穩定 id，例如 "large-v3-turbo"。
        repo_id: Hugging Face repo。
        revision: 固定的 40 碼 commit hash。
        files: 需要下載的檔案（含大小與 SHA-256）。
        languages: "multilingual" 表示 Whisper 的 99（large-v3 為 100）種語言。
        n_mels: mel 頻帶數，僅供說明；推論時以模型實際值為準。
        speed_tier: 本機 CPU int8 實測的相對速度等級。
        license: 權重授權（SPDX）。
        approx_cpu_ram_mb: CPU int8 推論時整個行程的峰值記憶體（本機實測，含 Python 基底約 0.2 GB）。
        approx_gpu_vram_mb: CUDA float16 載入＋暖機後的 VRAM 增量（含 CUDA context 約 0.5 GB）。
        approx_gpu_vram_int8_mb: CUDA int8_float16 時的 VRAM 增量。
        no_speech_reliable: `no_speech_probability` 能否用來過濾非語音；
            large-v3-turbo 實測在靜音與噪音上也幾乎都是 0，不能依賴。
    """

    id: str
    repo_id: str
    revision: str
    files: tuple[ModelFile, ...]
    languages: tuple[str, ...]
    n_mels: int
    speed_tier: SpeedTier
    license: str
    approx_cpu_ram_mb: int
    approx_gpu_vram_mb: int
    approx_gpu_vram_int8_mb: int
    no_speech_reliable: bool = True

    @property
    def total_bytes(self) -> int:
        """所有檔案的大小總和，供下載進度與磁碟空間檢查使用。"""
        return sum(f.size for f in self.files)

    def supports(self, language: str) -> bool:
        """模型是否能辨識指定語言。"""
        return self.languages == _MULTILINGUAL or language in self.languages


# Systran 的 tiny～medium 共用同一份 80-mel 分詞器與詞彙表
_V2_TOKENIZER = ModelFile(
    "tokenizer.json",
    2203239,
    "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
)
_V2_VOCAB = ModelFile(
    "vocabulary.txt",
    459861,
    "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
)
_V3_PREPROCESSOR = ModelFile(
    "preprocessor_config.json",
    340,
    "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
)
_V3_VOCAB = ModelFile(
    "vocabulary.json",
    1068114,
    "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
)

# 數值來源：Hugging Face API `api/models/<repo>/revision/<rev>?blobs=true`（LFS 檔的
# SHA-256）與實際下載小檔後計算的 SHA-256（2026-11-05）。RAM／VRAM 來自
# experiments/asr_benchmark 的本機量測（tiny／base／small 的 VRAM 受同時執行的 Ollama 干擾，
# 為「權重大小 + 約 0.5 GB context」的估計值）。
MODELS: dict[str, ModelSpec] = {
    spec.id: spec
    for spec in (
        ModelSpec(
            id="tiny",
            repo_id="Systran/faster-whisper-tiny",
            revision="d90ca5fe260221311c53c58e660288d3deb8d356",
            files=(
                ModelFile(
                    "config.json",
                    2249,
                    "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
                ),
                ModelFile(
                    "model.bin",
                    75538270,
                    "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
                ),
                _V2_TOKENIZER,
                _V2_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=80,
            speed_tier="fastest",
            license="MIT",
            approx_cpu_ram_mb=233,
            approx_gpu_vram_mb=500,
            approx_gpu_vram_int8_mb=400,
        ),
        ModelSpec(
            id="base",
            repo_id="Systran/faster-whisper-base",
            revision="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
            files=(
                ModelFile(
                    "config.json",
                    2309,
                    "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
                ),
                ModelFile(
                    "model.bin",
                    145217532,
                    "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
                ),
                _V2_TOKENIZER,
                _V2_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=80,
            speed_tier="fast",
            license="MIT",
            approx_cpu_ram_mb=348,
            approx_gpu_vram_mb=600,
            approx_gpu_vram_int8_mb=450,
        ),
        ModelSpec(
            id="small",
            repo_id="Systran/faster-whisper-small",
            revision="536b0662742c02347bc0e980a01041f333bce120",
            files=(
                ModelFile(
                    "config.json",
                    2370,
                    "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
                ),
                ModelFile(
                    "model.bin",
                    483546902,
                    "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
                ),
                _V2_TOKENIZER,
                _V2_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=80,
            speed_tier="medium",
            license="MIT",
            approx_cpu_ram_mb=950,
            approx_gpu_vram_mb=1000,
            approx_gpu_vram_int8_mb=650,
        ),
        ModelSpec(
            id="medium",
            repo_id="Systran/faster-whisper-medium",
            revision="08e178d48790749d25932bbc082711ddcfdfbc4f",
            files=(
                ModelFile(
                    "config.json",
                    2257,
                    "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
                ),
                ModelFile(
                    "model.bin",
                    1527906378,
                    "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
                ),
                _V2_TOKENIZER,
                _V2_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=80,
            speed_tier="slow",
            license="MIT",
            approx_cpu_ram_mb=1754,
            approx_gpu_vram_mb=2140,
            approx_gpu_vram_int8_mb=1180,
        ),
        ModelSpec(
            id="large-v3-turbo",
            # 原 mobiuslabsgmbh/faster-whisper-large-v3-turbo，已轉址到 dropbox-dash
            repo_id="dropbox-dash/faster-whisper-large-v3-turbo",
            revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
            files=(
                ModelFile(
                    "config.json",
                    2263,
                    "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                ),
                ModelFile(
                    "model.bin",
                    1617884929,
                    "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                ),
                _V3_PREPROCESSOR,
                ModelFile(
                    "tokenizer.json",
                    2710337,
                    "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                ),
                _V3_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=128,
            speed_tier="slow",
            license="MIT",
            approx_cpu_ram_mb=1890,
            approx_gpu_vram_mb=2300,
            approx_gpu_vram_int8_mb=1240,
            no_speech_reliable=False,
        ),
        ModelSpec(
            id="large-v3",
            repo_id="Systran/faster-whisper-large-v3",
            revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
            files=(
                ModelFile(
                    "config.json",
                    2394,
                    "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
                ),
                ModelFile(
                    "model.bin",
                    3087284237,
                    "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
                ),
                _V3_PREPROCESSOR,
                ModelFile(
                    "tokenizer.json",
                    2480617,
                    "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
                ),
                _V3_VOCAB,
            ),
            languages=_MULTILINGUAL,
            n_mels=128,
            speed_tier="slowest",
            license="MIT",
            approx_cpu_ram_mb=3282,
            approx_gpu_vram_mb=4170,
            approx_gpu_vram_int8_mb=2140,
        ),
    )
}


def get_model(model_id: str) -> ModelSpec:
    """依 id 取得模型；未知 id 丟 `KeyError` 並列出可用的 id。"""
    try:
        return MODELS[model_id]
    except KeyError:
        raise KeyError(
            f"unknown ASR model {model_id!r}; available: {', '.join(MODELS)}"
        ) from None
