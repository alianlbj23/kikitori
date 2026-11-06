"""預先量化的衍生快取：把快取中 float16 的 `model.bin` 離線量化成 int8，另存一份。

位於 asr 模組的快取層，介於下載（`download.py`，只管 registry 驗證過的原始檔）與推論
（`whisper.py`）之間。CTranslate2 以 int8 系列精度載入 float16 權重時，每次都要在 CPU 上
量化（large-v3 約 4 s）；衍生檔存的是量化後的權重，載入時就不必再做。

- 原始檔不動：衍生檔放在 `<cache>/<id>/derived/<key>/`，鍵含來源 `model.bin` 的 SHA-256、
  精度、CTranslate2 版本與本格式版本，任一不符就當作不存在（退回原始檔照常即時量化）。
- 不在「開始」的關鍵路徑上：session 照常從原始檔載入，`DerivedCacheWorker` 在所有 ASR 模型
  都關閉一段時間後才在背景轉換；轉換途中有 session 開始就中止，之後閒置時重做。
- 正確性把關：轉換後以固定輸入比對「載入衍生檔」與「從原始檔即時量化」的 `encode()` 與
  greedy 解碼輸出，逐位元相同才發布（暫存資料夾 → rename）；任何錯誤都記 log、寫下失敗標記
  （同一個鍵不再重試）。

`model.bin` 的格式沒有官方讀取 API：讀檔依 CTranslate2 4.8.5 C++ `src/models/model.cc` 的
`Model::load()`，寫檔與 Python `ctranslate2/specs/model_spec.py` 的 `ModelSpec._serialize()`
逐位元組相同（測試以官方函式對照；打包的引擎排除了 `ctranslate2.specs`，所以不能直接呼叫）。
量化規則依 C++ `Model::set_compute_type()`／`ensure_dtype()` 與 `cpu::quantize_s8()`，
而不是 Python 轉換器的 `LayerSpec._quantize()`：兩者對 conv 權重與全零列的處理不同，
衍生檔要與「執行時即時量化」的結果相同。依據與量測見 `docs/specs/asr.md`「預先量化的衍生快取」。
"""

import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import numpy.typing as npt

from kikitori.asr.download import (
    UnsafeModelPathError,
    _check_no_links,
    _is_link,
    _model_root,
    _scan_tree,
    is_downloaded,
    model_dir,
)
from kikitori.asr.registry import ModelSpec

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
"""衍生檔的格式版本；量化或檔案配置的做法改變時遞增，舊的衍生檔就不再被使用。"""
SUPPORTED_BINARY_VERSION = 6
"""CTranslate2 4.8.5 的 `model.bin` binary version（`model_spec.CURRENT_BINARY_VERSION`）。"""
SUPPORTED_SPECS = {"WhisperSpec": 3}
"""支援的 spec 名稱與 revision（C++ `WhisperModel::current_spec_revision()`）。"""
DERIVABLE_COMPUTE_TYPES = frozenset(
    {"int8", "int8_float16", "int8_float32", "int8_bfloat16"}
)
"""權重量化成 int8 的精度：只有這些載入時要量化，衍生檔才有收益。"""

DERIVED_SUBDIR = "derived"
DERIVED_MARKER = "kikitori-derived.json"
FAILED_SUFFIX = ".failed.json"
TMP_PREFIX = ".tmp-"
STALE_TMP_SECS = 3600.0
"""比這更舊的暫存資料夾視為中斷的殘留（轉換一個模型約 10–30 s），下一次轉換前刪掉。"""

MAX_VARIABLES = 100_000
MAX_ALIASES = 100_000
MAX_RANK = 8
MAX_MARKER_BYTES = 1 << 16
QUANTIZE_CHUNK_ROWS = 2048
"""量化時一次處理的列數：限制 float32 暫存陣列的大小（large-v3 的詞嵌入有 51866 列）。"""
COPY_CHUNK_BYTES = 8 << 20

# include/ctranslate2/types.h 的 DataType 順序（同 model_spec._dtype_to_type_id）
_DTYPE_NAMES = ("float32", "int8", "int16", "int32", "float16", "bfloat16")
_ITEM_SIZES = (4, 1, 2, 4, 2, 2)
_FLOAT32, _INT8, _FLOAT16, _BFLOAT16 = 0, 1, 4, 5
_NUMPY_FLOATS: dict[int, type[np.floating[Any]]] = {
    _FLOAT32: np.float32,
    _FLOAT16: np.float16,
}
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+_-]{1,32}$")


class ModelFormatError(ValueError):
    """`model.bin` 不是支援的格式（版本、spec、欄位範圍、截斷或多餘的資料）。"""


class DerivationCancelled(Exception):
    """轉換被中止（有 session 開始、模型要刪除或引擎結束）；不算失敗，之後會重做。"""


@dataclass(frozen=True)
class Variable:
    """`model.bin` 中的一個變數（只有標頭；資料留在檔案裡，用到時才讀）。"""

    name: str
    shape: tuple[int, ...]
    dtype_id: int
    offset: int
    """資料在檔案中的起點。"""
    num_bytes: int


@dataclass(frozen=True)
class ModelBinIndex:
    """`model.bin` 的結構：標頭、變數（依檔案順序）與別名。"""

    binary_version: int
    spec_name: str
    spec_revision: int
    variables: tuple[Variable, ...]
    aliases: tuple[tuple[str, str], ...]


class _Reader:
    """依 `model.cc` 的 `consume<T>()` 讀取，每次讀取都檢查不超出檔案。"""

    def __init__(self, file: BinaryIO, size: int) -> None:
        self._file = file
        self._size = size

    @property
    def position(self) -> int:
        return self._file.tell()

    def take(self, count: int) -> bytes:
        if count < 0 or count > self._size - self.position:
            raise ModelFormatError(
                f"model.bin is truncated: need {count} bytes at offset {self.position}"
            )
        data = self._file.read(count)
        if len(data) != count:
            raise ModelFormatError(f"short read at offset {self.position}")
        return data

    def u8(self) -> int:
        return int(self.take(1)[0])

    def u16(self) -> int:
        return int(struct.unpack("<H", self.take(2))[0])

    def u32(self) -> int:
        return int(struct.unpack("<I", self.take(4))[0])

    def string(self) -> str:
        # 長度含結尾的 \0（model_spec._write_string）；C++ 拒絕長度 0
        length = self.u16()
        if length == 0:
            raise ModelFormatError(f"invalid string length 0 at offset {self.position}")
        raw = self.take(length)
        if raw[-1] != 0:
            raise ModelFormatError(
                f"string at offset {self.position} is not terminated"
            )
        try:
            text = raw[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModelFormatError(f"string is not UTF-8: {exc}") from None
        if not text or "\0" in text:
            raise ModelFormatError(f"invalid name {text!r} at offset {self.position}")
        return text

    def skip(self, count: int) -> None:
        if count > self._size - self.position:
            raise ModelFormatError(
                f"variable payload of {count} bytes runs past the end of model.bin"
            )
        self._file.seek(count, os.SEEK_CUR)


def read_model_index(path: Path) -> ModelBinIndex:
    """讀取 `model.bin` 的結構（不讀權重資料），並檢查每個欄位的範圍。

    Raises:
        ModelFormatError: 不支援的 binary version／spec、欄位超出範圍、截斷或檔尾有多餘資料。
        OSError: 讀檔失敗。
    """
    size = path.stat().st_size
    with path.open("rb") as file:
        reader = _Reader(file, size)
        binary_version = reader.u32()
        if binary_version != SUPPORTED_BINARY_VERSION:
            raise ModelFormatError(
                f"unsupported model.bin binary version {binary_version} "
                f"(supported: {SUPPORTED_BINARY_VERSION})"
            )
        spec_name = reader.string()
        spec_revision = reader.u32()
        if SUPPORTED_SPECS.get(spec_name) != spec_revision:
            raise ModelFormatError(
                f"unsupported model spec {spec_name} revision {spec_revision}"
            )
        count = reader.u32()
        if count > MAX_VARIABLES:
            raise ModelFormatError(f"too many variables: {count}")
        variables: list[Variable] = []
        names: set[str] = set()
        for _ in range(count):
            name = reader.string()
            if name in names:
                raise ModelFormatError(f"duplicate variable {name}")
            names.add(name)
            rank = reader.u8()
            if rank > MAX_RANK:
                raise ModelFormatError(f"variable {name} has rank {rank}")
            shape = tuple(reader.u32() for _ in range(rank))
            if any(dim == 0 for dim in shape):
                raise ModelFormatError(f"variable {name} has an empty dimension")
            dtype_id = reader.u8()
            if dtype_id >= len(_DTYPE_NAMES):
                raise ModelFormatError(f"variable {name} has unknown dtype {dtype_id}")
            num_bytes = reader.u32()
            # Python 整數：np.prod 的 int64 會溢位回繞（例如 65536⁴ → 0）
            expected = math.prod(shape) * _ITEM_SIZES[dtype_id]
            if num_bytes != expected:
                raise ModelFormatError(
                    f"variable {name} has {num_bytes} bytes, expected {expected}"
                )
            offset = reader.position
            reader.skip(num_bytes)
            variables.append(Variable(name, shape, dtype_id, offset, num_bytes))
        alias_count = reader.u32()
        if alias_count > MAX_ALIASES:
            raise ModelFormatError(f"too many aliases: {alias_count}")
        aliases = tuple((reader.string(), reader.string()) for _ in range(alias_count))
        if reader.position != size:
            raise ModelFormatError(
                f"model.bin has {size - reader.position} unexpected trailing bytes"
            )
    return ModelBinIndex(
        binary_version, spec_name, spec_revision, tuple(variables), aliases
    )


def is_quantized_at_load(variable: Variable) -> bool:
    """這個變數在 int8 系列精度載入時會被量化成 int8（衍生檔就存量化後的結果）。

    C++ `Model::set_compute_type()`：名稱以 "weight" 結尾的是權重（`is_quantizable`）；
    名稱含 "conv" 的在 CUDA（與有 oneDNN 的 CPU）上不量化、改成浮點精度，所以衍生檔保留原值，
    執行時照原本的規則處理（沒有 oneDNN 的 CPU 會在載入時量化這兩個小權重，結果與從原始檔
    載入相同）。純量不量化。
    """
    return (
        variable.name.endswith("weight")
        and "conv" not in variable.name
        and len(variable.shape) >= 1
        and variable.dtype_id in _NUMPY_FLOATS
    )


def quantize_int8(
    values: npt.NDArray[Any],
) -> tuple[npt.NDArray[np.int8], npt.NDArray[np.float32]]:
    """依 CTranslate2 執行時的規則把浮點權重量化成 int8，回傳（權重, 每列的 scale）。

    C++ `ensure_dtype()` 先 `to_float32()`，再以 `ops::Quantize`（round_before_cast，binary
    version ≥ 5）呼叫 `cpu::quantize_s8()`：以最後一維為一列，`scale = 127 / amax`（amax 為 0
    時 1），`round(x × scale)`（就近取偶）後轉 int8。全部以 float32 計算，與 numpy 的
    `np.rint` 結果相同。

    Raises:
        ModelFormatError: 權重含 NaN 或無限大。
    """
    depth = values.shape[-1]
    rows = values.size // depth
    flat = values.reshape(rows, depth)
    quantized = np.empty((rows, depth), dtype=np.int8)
    scales = np.empty(rows, dtype=np.float32)
    for start in range(0, rows, QUANTIZE_CHUNK_ROWS):
        end = min(start + QUANTIZE_CHUNK_ROWS, rows)
        chunk = flat[start:end].astype(np.float32)
        amax = np.max(np.abs(chunk), axis=1)
        if not np.all(np.isfinite(amax)):
            raise ModelFormatError("weight contains NaN or infinity")
        scale = np.ones_like(amax)
        nonzero = amax != 0
        scale[nonzero] = np.float32(127.0) / amax[nonzero]
        np.multiply(chunk, scale[:, np.newaxis], out=chunk)
        np.rint(chunk, out=chunk)
        quantized[start:end] = chunk.astype(np.int8)
        scales[start:end] = scale
    return quantized.reshape(values.shape), scales


class _HashingWriter:
    """寫檔時同時計算 SHA-256 與位元組數。"""

    def __init__(self, file: BinaryIO) -> None:
        self.file = file
        self.sha256 = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes | memoryview) -> None:
        self.file.write(data)
        self.sha256.update(data)
        self.size += len(data)

    def string(self, text: str) -> None:
        # 同 model_spec._write_string()（名稱都是 ASCII，字元數等於位元組數）
        raw = text.encode("utf-8")
        self.write(struct.pack("<H", len(raw) + 1) + raw + b"\0")

    def header(
        self, name: str, shape: tuple[int, ...], dtype_id: int, size: int
    ) -> None:
        self.string(name)
        self.write(struct.pack("<B", len(shape)))
        for dim in shape:
            self.write(struct.pack("<I", dim))
        self.write(struct.pack("<B", dtype_id))
        self.write(struct.pack("<I", size))


@dataclass(frozen=True)
class WriteStats:
    """`write_quantized_model_bin()` 的結果。"""

    size: int
    sha256: str
    quantized: int
    """量化的變數數。"""


StopCheck = Callable[[], bool]


def _never_stop() -> bool:
    return False


def write_quantized_model_bin(
    source: Path, dest: Path, *, should_stop: StopCheck = _never_stop
) -> WriteStats:
    """把 `source` 的權重依執行時規則量化成 int8，寫成新的 `model.bin`（逐個變數處理）。

    變數依名稱排序寫出（同 `_serialize()` 的 `variables(ordered=True)`），每個量化的
    `<name>` 後面多一個 float32 的 `<name>_scale`；其他變數與別名原樣複製。

    Raises:
        ModelFormatError: 格式不支援，或沒有任何要量化的權重。
        DerivationCancelled: `should_stop()` 在兩個變數之間回傳 True。
        FileExistsError: `dest` 已存在。
        OSError: 讀寫失敗。
    """
    index = read_model_index(source)
    by_name = {v.name: v for v in index.variables}
    quantize = {v.name for v in index.variables if is_quantized_at_load(v)}
    if not quantize:
        raise ModelFormatError("model.bin has no float weights to quantize")
    taken = set(by_name) | {alias for alias, _ in index.aliases}
    for name in quantize:
        if f"{name}_scale" in taken:
            raise ModelFormatError(f"model.bin already has {name}_scale")
    for name, variable in by_name.items():
        if (
            variable.dtype_id == _BFLOAT16
            and name.endswith("weight")
            and "conv" not in name
        ):
            raise ModelFormatError(f"bfloat16 weight {name} is not supported")
    scale_of = {f"{name}_scale": name for name in quantize}
    order = sorted([*by_name, *scale_of])
    pending: dict[str, npt.NDArray[np.float32]] = {}

    with source.open("rb") as src, dest.open("xb") as out:
        writer = _HashingWriter(out)
        writer.write(struct.pack("<I", index.binary_version))
        writer.string(index.spec_name)
        writer.write(struct.pack("<I", index.spec_revision))
        writer.write(struct.pack("<I", len(order)))
        for name in order:
            if should_stop():
                raise DerivationCancelled(f"stopped before {name}")
            if name in scale_of:
                scale = pending.pop(name)
                writer.header(name, scale.shape, _FLOAT32, scale.nbytes)
                writer.write(memoryview(scale).cast("B"))
                continue
            variable = by_name[name]
            src.seek(variable.offset)
            if name not in quantize:
                writer.header(
                    name, variable.shape, variable.dtype_id, variable.num_bytes
                )
                remaining = variable.num_bytes
                while remaining:
                    chunk = src.read(min(COPY_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ModelFormatError(f"model.bin ended inside {name}")
                    writer.write(chunk)
                    remaining -= len(chunk)
                continue
            raw = np.empty(variable.num_bytes, dtype=np.uint8)
            if src.readinto(memoryview(raw)) != variable.num_bytes:
                raise ModelFormatError(f"model.bin ended inside {name}")
            values = raw.view(_NUMPY_FLOATS[variable.dtype_id]).reshape(variable.shape)
            weights, scales = quantize_int8(values)
            del raw, values
            writer.header(name, variable.shape, _INT8, weights.nbytes)
            writer.write(memoryview(np.ascontiguousarray(weights)).cast("B"))
            del weights
            pending[f"{name}_scale"] = scales
        writer.write(struct.pack("<I", len(index.aliases)))
        for alias, target in index.aliases:
            writer.string(alias)
            writer.string(target)
        out.flush()
        os.fsync(out.fileno())
    if pending:  # 不會發生：排序後 <name> 一定在 <name>_scale 之前
        raise ModelFormatError(f"scales were not written: {sorted(pending)}")
    return WriteStats(writer.size, writer.sha256.hexdigest(), len(quantize))


# --- 快取配置 -----------------------------------------------------------------


@dataclass(frozen=True)
class DerivedKey:
    """衍生檔的鍵：任一欄位不同就是另一份衍生檔（舊的不再使用）。"""

    model_id: str
    revision: str
    source_sha256: str
    """原始 `model.bin` 的 SHA-256（registry 的值，下載時已驗證）。"""
    compute_type: str
    ctranslate2_version: str
    format_version: int = FORMAT_VERSION

    @property
    def dirname(self) -> str:
        """資料夾名稱，例如 `0123456789abcdef-int8_float16-ct2-4.8.5-f1`。"""
        return (
            f"{self.source_sha256[:16]}-{self.compute_type}"
            f"-ct2-{self.ctranslate2_version}-f{self.format_version}"
        )

    def as_dict(self) -> dict[str, object]:
        """完成標記與失敗標記記錄的鍵欄位。"""
        return {
            "format_version": self.format_version,
            "model_id": self.model_id,
            "revision": self.revision,
            "source_sha256": self.source_sha256,
            "compute_type": self.compute_type,
            "ctranslate2_version": self.ctranslate2_version,
        }


def ctranslate2_version() -> str:
    """目前載入的 CTranslate2 版本。

    Raises:
        TypeError: 模組沒有版本字串。
    """
    import ctranslate2

    version = getattr(ctranslate2, "__version__", None)
    if not isinstance(version, str):
        raise TypeError(f"ctranslate2 has no version string: {version!r}")
    return version


def derived_key(
    spec: ModelSpec, compute_type: str, ct2_version: str | None = None
) -> DerivedKey | None:
    """`spec` 以 `compute_type` 載入時的衍生檔鍵；這個精度不需要衍生檔時 None。

    Raises:
        TypeError: CTranslate2 沒有版本字串。
        ValueError: CTranslate2 版本字串含不能放進資料夾名稱的字元。
    """
    if compute_type not in DERIVABLE_COMPUTE_TYPES:
        return None
    source = next((f for f in spec.files if f.name == "model.bin"), None)
    if source is None:
        return None
    version = ctranslate2_version() if ct2_version is None else ct2_version
    if not _VERSION_PATTERN.match(version):
        raise ValueError(f"unexpected CTranslate2 version string {version!r}")
    return DerivedKey(spec.id, spec.revision, source.sha256, compute_type, version)


def derived_root(spec: ModelSpec, cache_dir: Path) -> Path:
    """模型的衍生快取根資料夾 `<cache>/<id>/derived`（在 `models.delete` 刪除的範圍內）。"""
    return _model_root(spec, cache_dir) / DERIVED_SUBDIR


def derived_dir(spec: ModelSpec, cache_dir: Path, key: DerivedKey) -> Path:
    """一份衍生檔的資料夾。"""
    return derived_root(spec, cache_dir) / key.dirname


def _failed_marker(spec: ModelSpec, cache_dir: Path, key: DerivedKey) -> Path:
    return derived_root(spec, cache_dir) / f"{key.dirname}{FAILED_SUFFIX}"


def _read_json(path: Path) -> object:
    if path.stat().st_size > MAX_MARKER_BYTES:
        raise ValueError(f"{path} is larger than {MAX_MARKER_BYTES} bytes")
    return json.loads(path.read_text(encoding="utf-8"))


def find_derived(spec: ModelSpec, cache_dir: Path, key: DerivedKey) -> Path | None:
    """可用的衍生檔資料夾；沒有、標記不符或缺檔時 None（呼叫端改從原始檔載入）。

    與 `is_downloaded()` 相同只比對標記與檔案大小、不重算雜湊（驗證在發布前做過）。
    """
    directory = derived_dir(spec, cache_dir, key)
    marker = directory / DERIVED_MARKER
    try:
        if _is_link(directory) or not marker.is_file():
            return None
        recorded = _read_json(marker)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("ignoring unreadable derived model marker %s: %s", marker, exc)
        return None
    if not isinstance(recorded, dict) or recorded.get("key") != key.as_dict():
        return None
    files = recorded.get("files")
    if not isinstance(files, dict) or "model.bin" not in files:
        return None
    for name, size in files.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in ("", ".", "..")
        ):
            return None
        path = directory / name
        try:
            if not path.is_file() or path.stat().st_size != size:
                return None
        except OSError:
            return None
    return directory


def failure_reason(spec: ModelSpec, cache_dir: Path, key: DerivedKey) -> str | None:
    """這個鍵之前轉換失敗的原因；沒有失敗紀錄時 None。

    標記讀不到或內容不對時也當作失敗（寧可不重試，也不要每個 session 重做）。
    """
    marker = _failed_marker(spec, cache_dir, key)
    try:
        recorded = _read_json(marker)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return f"unreadable failure marker: {exc}"
    if isinstance(recorded, dict) and isinstance(recorded.get("reason"), str):
        return str(recorded["reason"])
    return "malformed failure marker"


def mark_failed(spec: ModelSpec, cache_dir: Path, key: DerivedKey, reason: str) -> None:
    """記下這個鍵轉換或載入失敗，之後不再嘗試（鍵改變時，例如升級 CTranslate2，才會重試）。

    Raises:
        OSError: 寫不出標記。
    """
    root = derived_root(spec, cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    marker = _failed_marker(spec, cache_dir, key)
    content = json.dumps(
        {"key": key.as_dict(), "reason": reason[:2000], "time": time.time()},
        ensure_ascii=False,
        indent=2,
    )
    _write_atomic(marker, content)
    logger.warning(
        "pre-quantized %s (%s) marked as failed; using the original model: %s",
        key.model_id,
        key.compute_type,
        reason,
    )


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    # 發布（rename）之前落盤：斷電後不會留下大小正確、內容是零的檔案（find_derived 只比大小）
    with path.open("rb+") as file:
        os.fsync(file.fileno())


def remove_tree(path: Path, root: Path) -> None:
    """刪除 `root` 之內的資料夾 `path`（不跟隨連結；途中發現連結就停止）。

    Raises:
        UnsafeModelPathError: `path` 是連結或不在 `root` 之內，或樹裡有連結。
        OSError: 刪除失敗。
    """
    if _is_link(path):
        raise UnsafeModelPathError(f"{path} is a link; refusing to delete it")
    if path.resolve(strict=True).parent != root.resolve(strict=True):
        raise UnsafeModelPathError(f"{path} is not directly inside {root}")
    _, files, dirs = _scan_tree(path, strict=True)
    for file in files:
        _check_no_links(file, path)
        file.unlink()
    for directory in reversed(dirs):
        _check_no_links(directory, path)
        directory.rmdir()


def _remove_stale_tmp(root: Path) -> None:
    """刪掉中斷（引擎被結束）留下的舊暫存資料夾；失敗只記 warning（不影響這次轉換）。"""
    now = time.time()
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return
    for entry in entries:
        if not entry.name.startswith(TMP_PREFIX) or _is_link(entry):
            continue
        try:
            if now - entry.stat().st_mtime < STALE_TMP_SECS:
                continue
            remove_tree(entry, root)
            logger.info("removed stale pre-quantization leftover %s", entry)
        except (OSError, UnsafeModelPathError) as exc:
            logger.warning("cannot remove stale leftover %s: %s", entry, exc)


# --- 正確性把關 ---------------------------------------------------------------

PROBE_SEED = 20261106
PROBE_FRAMES = 3000
PROBE_TOKENS = 24


@dataclass(frozen=True)
class ProbeResult:
    """固定輸入在一個模型上的輸出（比對用）。"""

    encoder: npt.NDArray[Any]
    tokens: tuple[int, ...]
    score: float
    no_speech_prob: float

    def same_as(self, other: "ProbeResult") -> bool:
        """逐位元相同（encoder 輸出比對位元組，不是數值近似）。"""
        return (
            self.encoder.dtype == other.encoder.dtype
            and self.encoder.shape == other.encoder.shape
            and self.encoder.tobytes() == other.encoder.tobytes()
            and self.tokens == other.tokens
            and struct.pack("<d", self.score) == struct.pack("<d", other.score)
            and struct.pack("<d", self.no_speech_prob)
            == struct.pack("<d", other.no_speech_prob)
        )


def probe_features(n_mels: int) -> npt.NDArray[np.float32]:
    """固定的假 log-mel 特徵（每次相同；值域與 Whisper 的特徵相近）。"""
    rng = np.random.default_rng(PROBE_SEED)
    return rng.uniform(-1.0, 1.5, size=(1, n_mels, PROBE_FRAMES)).astype(np.float32)


def probe_model(
    model: Any, prompt: list[int], storage: Callable[[Any], Any]
) -> ProbeResult:
    """對 `model`（`ctranslate2.models.Whisper`）跑固定輸入的 encode 與 greedy 解碼。

    encode 只用到 encoder 的權重；greedy 解碼（含 no_speech 機率）用到 decoder 與詞嵌入，
    兩者都比才涵蓋所有量化的權重。
    """
    features = storage(probe_features(int(model.n_mels)))
    encoded = np.array(model.encode(features, to_cpu=True))
    result = model.generate(
        features,
        [prompt],
        beam_size=1,
        max_length=len(prompt) + PROBE_TOKENS,
        return_scores=True,
        return_no_speech_prob=True,
        suppress_blank=False,
    )[0]
    return ProbeResult(
        encoder=encoded,
        tokens=tuple(int(t) for t in result.sequences_ids[0]),
        score=float(result.scores[0]),
        no_speech_prob=float(result.no_speech_prob),
    )


# --- 背景轉換 -----------------------------------------------------------------


class ActivityTracker:
    """ASR 模型的使用計數；背景轉換只在計數為 0（閒置）時進行。

    `whisper.load_transcriber()` 載入期間 +1，`_ModelLease`（借出的模型）存在期間 +1。計數代表
    「模型開著或正在載入」，不代表推論正在進行；`close()` 逾時（推論卡住）時借用紀錄照樣結束、
    計數照樣減少。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._active = 0
        self._idle_since = time.monotonic()

    def begin(self) -> None:
        """開始載入或借出一個模型。"""
        with self._cond:
            self._active += 1
            self._cond.notify_all()

    def end(self) -> None:
        """載入結束，或借出的模型關閉（最後一個共用者關閉時）。"""
        with self._cond:
            if self._active == 0:
                logger.error("ASR activity counter would go below zero; ignoring")
                return
            self._active -= 1
            if self._active == 0:
                self._idle_since = time.monotonic()
            self._cond.notify_all()

    @property
    def active(self) -> bool:
        """有模型正在使用。"""
        with self._cond:
            return self._active > 0

    def wait_idle(self, quiet_secs: float, stop: Callable[[], bool]) -> bool:
        """等到已經連續閒置 `quiet_secs` 秒；`stop()` 為 True 時回傳 False。"""
        with self._cond:
            while not stop():
                if self._active == 0:
                    remaining = self._idle_since + quiet_secs - time.monotonic()
                    if remaining <= 0:
                        return True
                    self._cond.wait(min(remaining, 1.0))
                else:
                    self._cond.wait(1.0)
            return False

    def notify(self) -> None:
        """喚醒等待中的 `wait_idle()`（停止條件改變時）。"""
        with self._cond:
            self._cond.notify_all()


@dataclass(frozen=True)
class DerivationJob:
    """一份要產生的衍生檔，以及驗證時用的裝置與精度（同 session 實際載入時）。"""

    spec: ModelSpec
    cache_dir: Path
    key: DerivedKey
    device: str
    cpu_threads: int = 0


ModelFactory = Callable[[Path, str, str, int], Any]
"""`(模型資料夾, device, compute_type, intra_threads)` → `ctranslate2.models.Whisper`。"""


def _create_ct2_model(path: Path, device: str, compute_type: str, threads: int) -> Any:
    import ctranslate2

    return ctranslate2.models.Whisper(
        os.fspath(path), device=device, compute_type=compute_type, intra_threads=threads
    )


def _storage(array: Any) -> Any:
    import ctranslate2

    return ctranslate2.StorageView.from_array(array)


def _probe_prompt(model_path: Path) -> list[int]:
    from kikitori.asr.tokenizer import WhisperTokenizer

    tok = WhisperTokenizer.from_file(model_path / "tokenizer.json")
    return [tok.sot, tok.language_token("en"), tok.transcribe, tok.no_timestamps]


@dataclass(frozen=True)
class DerivationResult:
    """一次成功轉換的統計（log 與量測用）。"""

    path: Path
    model_bin_bytes: int
    convert_secs: float
    verify_secs: float


def build_derived(
    job: DerivationJob,
    *,
    should_stop: StopCheck = _never_stop,
    create_model: ModelFactory = _create_ct2_model,
    storage: Callable[[Any], Any] = _storage,
) -> DerivationResult:
    """產生、驗證並發布一份衍生檔（暫存資料夾寫完、比對一致後才 rename 成正式名稱）。

    任何失敗或中止都刪掉暫存資料夾，不留半成品；已有可用的衍生檔時直接回傳它。

    Raises:
        DerivationCancelled: `should_stop()` 在步驟之間回傳 True。
        ModelFormatError: 原始 `model.bin` 格式不支援。
        RuntimeError: 衍生檔的輸出與即時量化不同，或模型載入／推論失敗。
        FileNotFoundError: 原始模型沒有下載。
        OSError、UnsafeModelPathError: 寫檔、rename 或刪除失敗。
    """
    spec, cache, key = job.spec, job.cache_dir, job.key
    if not is_downloaded(spec, cache):
        raise FileNotFoundError(f"ASR model {spec.id} is not downloaded to {cache}")
    final = derived_dir(spec, cache, key)
    existing = find_derived(spec, cache, key)
    if existing is not None:
        return DerivationResult(existing, (existing / "model.bin").stat().st_size, 0, 0)
    source_dir = model_dir(spec, cache)
    root = derived_root(spec, cache)
    root.mkdir(parents=True, exist_ok=True)
    if _is_link(root):
        raise UnsafeModelPathError(f"{root} is a link; refusing to write into it")
    _remove_stale_tmp(root)
    if final.exists() or _is_link(final):
        # 標記不符或缺檔的舊資料夾（例如上次 rename 後標記被改壞）：刪掉重做
        logger.warning("replacing invalid pre-quantized model folder %s", final)
        remove_tree(final, root)

    tmp = root / f"{TMP_PREFIX}{key.dirname}-{os.getpid()}-{secrets.token_hex(4)}"
    tmp.mkdir()
    try:
        started = time.perf_counter()
        stats = write_quantized_model_bin(
            source_dir / "model.bin", tmp / "model.bin", should_stop=should_stop
        )
        for file in spec.files:
            if file.name != "model.bin":
                shutil.copyfile(source_dir / file.name, tmp / file.name)
                _fsync_file(tmp / file.name)
        convert_secs = time.perf_counter() - started
        if should_stop():
            raise DerivationCancelled("stopped before verification")

        started = time.perf_counter()
        _verify(job, source_dir, tmp, should_stop, create_model, storage)
        verify_secs = time.perf_counter() - started

        files = {"model.bin": stats.size}
        files.update({f.name: f.size for f in spec.files if f.name != "model.bin"})
        marker = {
            "key": key.as_dict(),
            "files": files,
            "model_bin_sha256": stats.sha256,
            "quantized_variables": stats.quantized,
            "verified_on": {"device": job.device, "compute_type": key.compute_type},
            "time": time.time(),
        }
        _write_atomic(tmp / DERIVED_MARKER, json.dumps(marker, indent=2))
        if should_stop():
            raise DerivationCancelled("stopped before publishing")
        try:
            os.rename(tmp, final)
        except OSError:
            # 另一個引擎行程剛好發布了同一個鍵
            if find_derived(spec, cache, key) is None:
                raise
            logger.info("pre-quantized %s was published by another process", final)
        logger.info(
            "pre-quantized %s (%s): %d MB, convert %.1f s, verify %.1f s",
            spec.id,
            key.compute_type,
            stats.size >> 20,
            convert_secs,
            verify_secs,
        )
        return DerivationResult(final, stats.size, convert_secs, verify_secs)
    finally:
        if tmp.exists():
            try:
                remove_tree(tmp, root)
            except (OSError, UnsafeModelPathError):
                logger.exception("cannot remove pre-quantization temp folder %s", tmp)


def _verify(
    job: DerivationJob,
    source_dir: Path,
    candidate_dir: Path,
    should_stop: StopCheck,
    create_model: ModelFactory,
    storage: Callable[[Any], Any],
) -> None:
    """依序載入原始檔（即時量化）與衍生檔（一次只佔一個模型的顯示記憶體），比對輸出。

    Raises:
        RuntimeError: 輸出不同。
        DerivationCancelled: 兩次載入之間 `should_stop()` 為 True。
    """
    prompt = _probe_prompt(source_dir)
    results: list[ProbeResult] = []
    for directory in (source_dir, candidate_dir):
        if should_stop():
            raise DerivationCancelled("stopped during verification")
        model = create_model(
            directory, job.device, job.key.compute_type, job.cpu_threads
        )
        try:
            results.append(probe_model(model, prompt, storage))
        finally:
            del model
    reference, candidate = results
    if not candidate.same_as(reference):
        raise RuntimeError(
            "pre-quantized model output differs from on-the-fly quantization "
            f"(tokens {reference.tokens[:8]} vs {candidate.tokens[:8]}, "
            f"score {reference.score!r} vs {candidate.score!r})"
        )


IDLE_SECS = 15.0
"""所有 ASR 模型關閉後要連續閒置多久才開始轉換（session 停止後馬上又開始時不搶資源）。"""
CANCEL_WAIT_SECS = 20.0
"""`cancel_model()` 預設等多久（刪除模型在引擎主執行緒上；比對時載入 large-v3 的原始檔約 7 s）。"""


class DerivedCacheWorker:
    """背景產生衍生檔的單一工作執行緒（行程內共用 `DERIVED_CACHE`）。

    `request()` 只排入佇列；執行緒等到 ASR 閒置 `idle_secs` 秒才轉換，期間每個變數之間
    檢查：有模型開始使用、`cancel_model()` 或 `shutdown()` 就中止（刪暫存、不記失敗）。
    被 session 中止的工作放回佇列，下次閒置時重做。其他錯誤記 log 並寫下失敗標記。
    """

    def __init__(
        self,
        activity: ActivityTracker,
        *,
        idle_secs: float = IDLE_SECS,
        build: Callable[..., DerivationResult] = build_derived,
    ) -> None:
        """
        Args:
            activity: ASR 模型使用中的計數。
            idle_secs: 開始轉換前要連續閒置的秒數。
            build: 產生衍生檔的函式（測試替換）。
        """
        self._activity = activity
        self._idle_secs = idle_secs
        self._build = build
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._pending: list[DerivationJob] = []
        self._running: DerivationJob | None = None
        self._cancelled: set[str] = set()
        self._stopping = False
        self._thread: threading.Thread | None = None

    def request(self, job: DerivationJob) -> bool:
        """排入一份衍生檔（已在佇列、正在做、已存在或之前失敗時不排），回傳是否排入。"""
        if failure_reason(job.spec, job.cache_dir, job.key) is not None:
            return False
        if find_derived(job.spec, job.cache_dir, job.key) is not None:
            return False
        with self._lock:
            if self._stopping:
                return False
            keys = [j.key for j in self._pending]
            if self._running is not None:
                keys.append(self._running.key)
            if job.key in keys:
                return False
            self._cancelled.discard(job.spec.id)
            self._pending.append(job)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="asr-prequantize", daemon=True
                )
                self._thread.start()
            self._changed.notify_all()
        logger.info(
            "queued pre-quantization of %s (%s) for when ASR is idle",
            job.spec.id,
            job.key.compute_type,
        )
        return True

    def pending(self) -> list[DerivationJob]:
        """佇列中與正在做的工作（測試與診斷用）。"""
        with self._lock:
            jobs = list(self._pending)
            if self._running is not None:
                jobs.insert(0, self._running)
            return jobs

    def cancel_model(self, model_id: str, timeout: float = CANCEL_WAIT_SECS) -> bool:
        """丟掉這個模型排隊中的工作，並等正在做的停下（刪除模型之前）。

        Returns:
            True 表示沒有這個模型的工作在執行了；False 表示等了 `timeout` 秒仍在執行
            （例如正在載入模型比對，CTranslate2 的載入不能中斷）。
        """
        deadline = time.monotonic() + timeout
        with self._lock:
            self._pending = [j for j in self._pending if j.spec.id != model_id]
            self._cancelled.add(model_id)
        # 在 self._lock 之外：等待閒置的執行緒持有 activity 的鎖時會呼叫 _stop_requested()
        self._activity.notify()
        with self._lock:
            while self._running is not None and self._running.spec.id == model_id:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True

    def shutdown(self, timeout: float = 5.0) -> None:
        """停止執行緒（引擎結束）；正在做的工作在下一個檢查點中止並刪掉暫存。"""
        with self._lock:
            self._stopping = True
            self._pending.clear()
            thread = self._thread
            self._changed.notify_all()
        self._activity.notify()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("pre-quantization did not stop within %.1f s", timeout)

    def _stop_requested(self, job: DerivationJob) -> bool:
        with self._lock:
            return self._stopping or job.spec.id in self._cancelled

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._pending and not self._stopping:
                    self._changed.wait()
                if self._stopping:
                    return
                job = self._pending[0]
            if not self._activity.wait_idle(
                self._idle_secs, lambda job=job: self._stop_requested(job)
            ):
                with self._lock:
                    if self._stopping:
                        return
                    if job in self._pending:
                        self._pending.remove(job)
                continue
            with self._lock:
                if job not in self._pending:
                    continue
                self._pending.remove(job)
                self._running = job
            try:
                self._run_job(job)
            finally:
                with self._lock:
                    self._running = None
                    self._changed.notify_all()

    def _run_job(self, job: DerivationJob) -> None:
        def should_stop() -> bool:
            return self._stop_requested(job) or self._activity.active

        logger.info(
            "starting pre-quantization of %s (%s)", job.spec.id, job.key.compute_type
        )
        try:
            result = self._build(job, should_stop=should_stop)
        except DerivationCancelled as exc:
            requeue = not self._stop_requested(job)
            logger.info(
                "pre-quantization of %s (%s) interrupted (%s)%s",
                job.spec.id,
                job.key.compute_type,
                exc,
                "; will retry when ASR is idle" if requeue else "",
            )
            if requeue:
                with self._lock:
                    self._pending.insert(0, job)
            return
        except Exception as exc:
            logger.exception(
                "pre-quantization of %s (%s) failed", job.spec.id, job.key.compute_type
            )
            if self._stop_requested(job):
                return  # 例如模型正在刪除：資料夾消失造成的錯誤不記成失敗
            if self._activity.active:
                # 失敗時有 session 在載入或執行：可能是兩者搶資源（例如驗證時顯示記憶體
                # 不足），不算這個鍵的問題；放回佇列，閒置時重做
                logger.warning(
                    "pre-quantization of %s failed while ASR was in use; will retry",
                    job.spec.id,
                )
                with self._lock:
                    if not self._stopping:
                        self._pending.insert(0, job)
                return
            try:
                mark_failed(
                    job.spec, job.cache_dir, job.key, f"{type(exc).__name__}: {exc}"
                )
            except OSError:
                logger.exception("cannot record the pre-quantization failure")
            return
        logger.info("pre-quantized model ready at %s", result.path)


ASR_ACTIVITY = ActivityTracker()
"""行程內開著的 ASR 模型（`whisper._ModelLease` 計數）。"""
DERIVED_CACHE = DerivedCacheWorker(ASR_ACTIVITY)
"""行程內共用的背景轉換（`load_transcriber(derived_cache=True)` 排入）。"""
