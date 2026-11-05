"""暫時的 CI 診斷（找出 Linux CI 上假模式 session 沒有字幕的原因後刪除）。

只印資訊、不判定成敗。
"""

import logging
import sys
import unittest

import numpy as np

from kikitori.config import Settings
from kikitori.engine.testing import (
    SPEECH_DEVICE,
    AmplitudeVoiceModel,
    _speech_track,
    fake_services,
)
from kikitori.pipeline import Pipeline
from kikitori.pipeline.factory import pipeline_config
from kikitori.vad import PauseEndpoint, Segmenter


def _p(*args: object) -> None:
    print("[ci-diag]", *args, file=sys.stderr, flush=True)


class CiDiagnostic(unittest.TestCase):
    def test_dump(self) -> None:
        try:
            np.show_runtime()
        except Exception as exc:  # noqa: BLE001 - 診斷用，印出即可
            _p("show_runtime failed", exc)
        track = _speech_track()
        model = AmplitudeVoiceModel()
        windows = [track[i : i + 512] for i in range(0, len(track) - 511, 512)]
        probs = [model.probability(w) for w in windows]
        _p(
            "track",
            track.dtype,
            track.shape,
            int(track.max()),
            "speech windows",
            sum(probs),
        )
        settings = Settings()
        config = pipeline_config(settings, "media")
        _p("config", config)
        seg = Segmenter(
            model, endpoint=PauseEndpoint(config.pause_secs, config.max_secs)
        )
        events = seg.process(track) + seg.flush()
        _p(
            "segment events",
            [type(e).__name__ for e in events if type(e).__name__ != "UtteranceAudio"],
        )

        handler = logging.StreamHandler(sys.stderr)
        log = logging.getLogger("kikitori")
        log.addHandler(handler)
        old = log.level
        log.setLevel(logging.DEBUG)
        try:
            services = fake_services()
            components = services.build_components(settings, "media")
            updates: list[object] = []
            statuses: list[object] = []
            pipeline = Pipeline(
                components,
                config,
                on_subtitle=updates.append,
                on_status=lambda s, d: statuses.append((s, d)),
            )
            pipeline.start(services.open_source(SPEECH_DEVICE, "loopback"))
            _p("threaded wait", pipeline.wait(15))
            _p("threaded statuses", statuses)
            _p("threaded updates", updates)
        finally:
            log.setLevel(old)
            log.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
