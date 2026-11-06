"""引擎的振假名：決定何時載入／釋放辭典，並替日文字幕算出 `ruby`。

位於 engine 層，介於伺服器與 `kikitori.furigana` 之間。規則（`docs/specs/furigana.md`）：

- 只對「目前的 session」、設定開啟、字幕原文是日文（`source_language` 為 ja，偵測或指定）
  的字幕標註；其他字幕不帶 `ruby`。
- 辭典在**第一句需要標註的字幕**時才載入（mmap，約 0.04 秒），所以從不出現日文的 session
  完全不載入。session 結束、執行中關閉振假名、刪除辭典之前都會釋放。
- 辭典沒下載、載入或分析失敗：該句不帶 `ruby`，原因記一次 warning（同一個 session 不重複），
  字幕照常送出——振假名是輔助顯示，不能讓字幕停下來。
"""

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from kikitori.furigana.dictionary import DictionaryStatus
from kikitori.furigana.ruby import RubySpan
from kikitori.pipeline.languages import base_language

logger = logging.getLogger(__name__)

RUBY_LANGUAGE = "ja"


class RubyAnalyzer(Protocol):
    """產生振假名的分析器（真實實作是 `furigana.analyzer.FuriganaAnalyzer`）。"""

    def ruby(self, text: str) -> list[RubySpan]:
        """`text` 的振假名。失敗時丟例外。"""
        ...

    def close(self) -> None:
        """釋放辭典；可重複呼叫。"""
        ...


class FuriganaProvider(Protocol):
    """振假名辭典的狀態、下載、刪除與開啟（測試換成假的實作）。"""

    def status(self) -> DictionaryStatus:
        """是否已安裝、大小與授權（只看檔案，很快）。"""
        ...

    def install(
        self, progress: Callable[[int, int], None], cancel: threading.Event
    ) -> None:
        """下載並安裝辭典；`cancel` 被設定時停止。失敗時丟例外。"""
        ...

    def delete(self) -> int:
        """刪除辭典，回傳釋放的位元組數。

        Raises:
            FileNotFoundError: 沒有安裝。
            OSError、ValueError: 刪除失敗或路徑不安全。
        """
        ...

    def open(self) -> RubyAnalyzer:
        """開啟辭典。

        Raises:
            FileNotFoundError: 沒有安裝。
            Exception: 其他載入錯誤（sudachipy 無法載入、辭典格式不符）。
        """
        ...


class FuriganaService:
    """持有分析器；依 session 與開關載入、釋放。所有方法都是執行緒安全的。"""

    def __init__(self, provider: FuriganaProvider) -> None:
        self._provider = provider
        # 開啟分析器、使用、關閉、刪除辭典都在這把鎖內：刪除時不會有人正在開啟同一個檔案
        self._lock = threading.Lock()
        self._analyzer: RubyAnalyzer | None = None
        self._session_id: int | None = None
        self._enabled = False
        self._unavailable = False
        """這個 session 已經回報過「無法標註」（不再重試、不再記 warning）。"""

    @property
    def provider(self) -> FuriganaProvider:
        """辭典的提供者。"""
        return self._provider

    @property
    def in_use(self) -> bool:
        """辭典是否正被開啟（`models.delete` 的「使用中」）。"""
        with self._lock:
            return self._analyzer is not None

    def begin_session(self, session_id: int, enabled: bool) -> None:
        """新的 session 開始：之後只標註這個 session 的字幕。"""
        with self._lock:
            self._close_locked()
            self._session_id = session_id
            self._enabled = enabled
            self._unavailable = False

    def end_session(self, session_id: int) -> None:
        """session 結束：釋放辭典（只有目前的 session 才算；重複呼叫無妨）。"""
        with self._lock:
            if self._session_id != session_id:
                return
            self._session_id = None
            self._close_locked()

    def set_enabled(self, enabled: bool) -> None:
        """執行中切換；關閉時立即釋放辭典，開啟時下一句日文字幕才載入。"""
        with self._lock:
            self._enabled = enabled
            self._unavailable = False
            if not enabled:
                self._close_locked()

    def dictionary_changed(self) -> None:
        """辭典下載完成（或失敗）：之前「沒有辭典」的 session 下一句再試一次。"""
        with self._lock:
            self._unavailable = False

    def delete_dictionary(self) -> int:
        """刪除辭典；正被開啟時丟 `DictionaryInUseError`（不關閉，由使用者先關掉振假名）。

        Raises:
            DictionaryInUseError: 辭典正被開啟。
            FileNotFoundError、OSError、ValueError: 見 `FuriganaProvider.delete()`。
        """
        with self._lock:
            if self._analyzer is not None:
                raise DictionaryInUseError(
                    f"the furigana dictionary is open for session {self._session_id}"
                )
            return self._provider.delete()

    def annotate(
        self, session_id: int, text: str, source_language: str
    ) -> list[RubySpan] | None:
        """這句字幕的振假名；不該標註或無法標註時 None（事件就不帶 `ruby`）。

        Args:
            session_id: 字幕所屬的 session。
            text: 字幕原文。
            source_language: 字幕原文的語言（Whisper 語言碼）。
        """
        if base_language(source_language) != RUBY_LANGUAGE:
            return None
        with self._lock:
            if not self._enabled or self._unavailable or session_id != self._session_id:
                return None
            if self._analyzer is None:
                try:
                    self._analyzer = self._provider.open()
                except FileNotFoundError as exc:
                    self._unavailable = True
                    logger.warning(
                        "furigana is on but the dictionary is not downloaded (%s); "
                        "download it with models.download %r",
                        exc,
                        "furigana-dictionary",
                    )
                    return None
                except Exception:
                    self._unavailable = True
                    logger.exception(
                        "cannot open the furigana dictionary; showing subtitles without it"
                    )
                    return None
                logger.info("furigana dictionary loaded for session %d", session_id)
            try:
                return self._analyzer.ruby(text)
            except Exception:
                self._unavailable = True
                logger.exception(
                    "furigana analysis failed; showing subtitles without it"
                )
                self._close_locked()
                return None

    def close(self) -> None:
        """引擎結束：釋放辭典。"""
        with self._lock:
            self._session_id = None
            self._close_locked()

    def _close_locked(self) -> None:
        analyzer, self._analyzer = self._analyzer, None
        if analyzer is None:
            return
        try:
            analyzer.close()
        except Exception:
            # 關閉失敗只影響記憶體何時歸還，不影響字幕
            logger.exception("closing the furigana dictionary failed")
        else:
            logger.info("furigana dictionary released")


class DictionaryInUseError(RuntimeError):
    """辭典正被開啟（session 執行中且振假名開啟），不能刪除。"""


class DefaultFuriganaProvider:
    """真實的實作：模型快取旁的 `furigana/` 與 Sudachi 分析器。"""

    def status(self) -> DictionaryStatus:
        """見 `furigana.dictionary.dictionary_status()`。"""
        from kikitori.furigana.dictionary import dictionary_status

        return dictionary_status()

    def install(
        self, progress: Callable[[int, int], None], cancel: threading.Event
    ) -> None:
        """見 `furigana.dictionary.install_dictionary()`。"""
        from kikitori.furigana.dictionary import install_dictionary

        install_dictionary(progress=progress, cancel=cancel)

    def delete(self) -> int:
        """見 `furigana.dictionary.delete_dictionary()`。"""
        from kikitori.furigana.dictionary import delete_dictionary

        return delete_dictionary()

    def open(self) -> RubyAnalyzer:
        """開啟已安裝的辭典（沒安裝時 `FileNotFoundError`）。"""
        from kikitori.furigana.analyzer import FuriganaAnalyzer
        from kikitori.furigana.dictionary import (
            default_root,
            dictionary_path,
            is_installed,
        )

        root = default_root()
        if not is_installed(root):
            raise FileNotFoundError(
                f"the furigana dictionary is not installed in {root}"
            )
        return FuriganaAnalyzer(dictionary_path(root))
