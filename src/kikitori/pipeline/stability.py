"""即時字幕的穩定前綴（LocalAgreement-2）與「何時重送即時翻譯」。

位於 pipeline 的即時路徑內部，只由即時 worker（或逐步模式的呼叫端）使用，不需要鎖。
whisper_streaming（Macháček et al., 2023）的 LocalAgreement-n：連續 n 次辨識結果的最長
共同前綴視為已確定。這裡取 n = 2、在文字層比較（即時路徑每次都重新辨識整句音訊，沒有
token 對齊可用），只用來告訴 UI 哪一段不會再變（`stable_chars`），以及減少即時翻譯請求：
穩定部分沒有變長時，重新翻譯只會讓譯文閃爍、佔用翻譯伺服器。
"""


def common_prefix_length(a: str, b: str) -> int:
    """兩個字串的最長共同前綴長度（Python 字元數）。"""
    limit = min(len(a), len(b))
    for index in range(limit):
        if a[index] != b[index]:
            return index
    return limit


class PartialStability:
    """追蹤目前這句的上一則即時原文、上一次即時翻譯請求與最後的即時譯文。

    即時路徑依句子順序處理，所以只記一句；換句子時自動重設，記憶體不會隨 session 成長。
    """

    def __init__(self) -> None:
        self._utterance_id: int | None = None
        self._previous: str | None = None
        self._requested_stable: int | None = None
        self._requested_text: str | None = None
        self.translation = ""
        """這句最後送出的即時譯文；不重送翻譯時沿用（譯文不清空、不閃爍）。"""

    def observe(self, utterance_id: int, original: str) -> int:
        """記下這則即時原文，回傳它的 `stable_chars`。

        Args:
            utterance_id: 句子編號；與上一則不同時重設（新句子的第一則為 0）。
            original: 顯示用的原文（與 `SubtitleUpdate.original` 相同的處理）。

        Returns:
            與同一句上一則即時原文的最長共同前綴長度；不會超過 `len(original)`。
        """
        if utterance_id != self._utterance_id:
            self._utterance_id = utterance_id
            self._previous = None
            self._requested_stable = None
            self._requested_text = None
            self.translation = ""
        stable = (
            0
            if self._previous is None
            else common_prefix_length(self._previous, original)
        )
        self._previous = original
        return stable

    def should_translate(self, original: str, stable: int) -> bool:
        """這則即時字幕要不要重送翻譯請求。

        這句的第一個請求一定送（穩定前綴要等第二則才知道，不能讓第一則沒有譯文）；之後只在
        穩定前綴比上一次請求時變長、且原文與上一次請求不同時才送。所以請求數不會多於以前
        「每則都送」，閃爍的尾巴（穩定部分不變）不再觸發翻譯。
        """
        if self._requested_stable is None:
            return True
        return stable > self._requested_stable and original != self._requested_text

    def mark_requested(self, original: str, stable: int) -> None:
        """記下送出翻譯請求時的原文與穩定前綴（失敗也算，避免每則都重試）。"""
        self._requested_stable = stable
        self._requested_text = original
