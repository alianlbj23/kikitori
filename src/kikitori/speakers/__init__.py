"""說話者標籤：多人說話時，替每句完整字幕標出「說話者 1／2／3…」。

位於 pipeline 的完整路徑旁：每句音訊算一個說話者向量（`embedding`，3D-Speaker CAM++，onnxruntime），
再線上分群（`clustering`）；`labeler.load_labeler()` 把兩者組成一個 session 用的 labeler。
模型延後下載（`model`）。規格見 `docs/specs/speaker-labels.md`。

這裡刻意不匯入子模組：引擎啟動時只需要 `model` 的 id 與狀態，不必載入 onnxruntime。
"""
