"""遠端運算（docs/specs/remote-compute.md）：聲音與字幕在本機（A），辨識與翻譯在另一台電腦（B）。

位於 engine 之下、pipeline 之旁：B 端把網路收到的音訊交給現有的 Pipeline，A 端把 Pipeline 的事件換成
從網路收到的事件。協定、憑證、配對與連線分在各子模組。
"""
