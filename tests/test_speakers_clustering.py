"""線上說話者分群的規則：自動、指定人數、短句、門檻邊界與輸入驗證。"""

import unittest

import numpy as np

from kikitori.speakers.clustering import MAX_SPEAKERS, OnlineSpeakers


def _unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    return v / np.linalg.norm(v)


A = _unit(1, 0, 0)
B = _unit(0, 1, 0)
C = _unit(0, 0, 1)
NEAR_A = _unit(1, 0.2, 0)  # 與 A 的相似度約 0.98


class OnlineSpeakersTest(unittest.TestCase):
    def test_auto_opens_a_new_speaker_for_each_new_voice(self) -> None:
        s = OnlineSpeakers()
        self.assertEqual(
            [s.assign(v, 2.0) for v in (A, B, NEAR_A, C, B)], [1, 2, 1, 3, 2]
        )
        self.assertEqual(s.speakers, 3)

    def test_fixed_count_stops_opening_and_takes_the_closest(self) -> None:
        s = OnlineSpeakers(2)
        self.assertEqual([s.assign(v, 2.0) for v in (A, B)], [1, 2])
        # 第三個聲音：已開滿 2 人，歸給最像的（C 與 A、B 一樣遠時取第一個）
        self.assertEqual(s.assign(_unit(0.1, 0, 1), 2.0), 1)
        self.assertEqual(s.speakers, 2)

    def test_auto_still_caps_at_max_speakers(self) -> None:
        s = OnlineSpeakers()
        voices = np.eye(MAX_SPEAKERS + 1, dtype=np.float32)
        labels = [s.assign(v, 2.0) for v in voices]
        self.assertEqual(labels[:MAX_SPEAKERS], list(range(1, MAX_SPEAKERS + 1)))
        self.assertIn(labels[-1], range(1, MAX_SPEAKERS + 1))
        self.assertEqual(s.speakers, MAX_SPEAKERS)

    def test_threshold_boundary_is_inclusive(self) -> None:
        at = _unit(0.6, 0.8, 0)  # 與 A 的相似度剛好 0.6
        below = _unit(0.59, 0.807, 0)
        for voice, expected in ((at, 1), (below, 2)):
            s = OnlineSpeakers(threshold=0.6)
            s.assign(A, 2.0)
            self.assertEqual(s.assign(voice, 2.0), expected)

    def test_short_utterances_never_open_or_move_a_speaker(self) -> None:
        s = OnlineSpeakers()
        self.assertIsNone(s.assign(A, 0.5))  # 第一句就很短：不標
        self.assertEqual(s.speakers, 0)
        s.assign(A, 2.0)
        self.assertEqual(s.assign(NEAR_A, 0.5), 1)  # 夠像：標成已知的人
        self.assertIsNone(s.assign(B, 0.5))  # 不像：不標、不開新的人
        self.assertEqual(s.speakers, 1)

    def test_short_utterance_takes_the_closest_once_a_fixed_count_is_full(self) -> None:
        s = OnlineSpeakers(2)
        s.assign(A, 2.0)
        self.assertIsNone(s.assign(B, 0.5))  # 還沒開滿：短句不開新的人
        s.assign(B, 2.0)
        self.assertEqual(s.assign(_unit(0.1, 0, 1), 0.5), 1)

    def test_centers_follow_the_running_sum(self) -> None:
        s = OnlineSpeakers(threshold=0.9)
        s.assign(A, 2.0)
        s.assign(_unit(1, 0.45, 0), 2.0)  # 相似度約 0.91：併入，中心往 B 偏
        # 單看 A 只有 0.86，但對新的中心（A 與上一句的和）超過 0.9
        self.assertEqual(s.assign(_unit(1, 0.6, 0), 2.0), 1)

    def test_rejects_bad_arguments(self) -> None:
        for count in (1, MAX_SPEAKERS + 1):
            with self.assertRaises(ValueError):
                OnlineSpeakers(count)
        with self.assertRaises(ValueError):
            OnlineSpeakers(threshold=1.5)
        with self.assertRaises(ValueError):
            OnlineSpeakers(min_secs=-1)
        s = OnlineSpeakers()
        s.assign(A, 2.0)
        with self.assertRaises(ValueError):
            s.assign(_unit(1, 0), 2.0)  # 維度不同
        with self.assertRaises(ValueError):
            s.assign(np.array([np.nan, 0, 0], dtype=np.float32), 2.0)
        with self.assertRaises(ValueError):
            s.assign(np.zeros((1, 3), dtype=np.float32), 2.0)


if __name__ == "__main__":
    unittest.main()
