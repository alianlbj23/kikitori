"""依語言的幻覺片語表（`experiments/hallucination_lexicon` 的實測結果）接進過濾後的行為。

- hard 片語：不論辨識出哪個語言都濾掉（Whisper 對套話的語言標籤不可靠）。
- soft 片語：只在辨識語言相同、而且 no_speech 高於門檻時才濾。
- 回歸：各語言的一般句子（含與片語相近的句子）、`experiments/translate_benchmark` 的
  台詞，以及本機有 `experiments/asr_benchmark/results` 時的真實日語辨識結果，都不會因
  片語表被濾掉。
"""

import json
import unicodedata
import unittest
from pathlib import Path

from kikitori.asr.filtering import (
    ANY_LANGUAGE,
    DEFAULT_HALLUCINATIONS,
    FilterConfig,
    normalize_for_match,
    rejection_reason,
)
from kikitori.asr.hallucination_lexicon import HALLUCINATION_LEXICON
from kikitori.asr.types import Transcript
from kikitori.languages import WHISPER_CODES

ROOT = Path(__file__).resolve().parents[1]
LEXICON_JSON = ROOT / "experiments" / "hallucination_lexicon" / "lexicon.json"
TRANSLATE_LINES = ROOT / "experiments" / "translate_benchmark" / "lines.json"
ASR_RESULTS = ROOT / "experiments" / "asr_benchmark" / "results"

CONFIG = FilterConfig()
SOFT_NO_SPEECH = CONFIG.soft_no_speech_threshold + 0.1
"""高於 soft 門檻、但低於其他靜音規則（0.6 + logprob、0.8）的 no_speech。"""


def t(text: str, language: str, no_speech: float = 0.02) -> Transcript:
    return Transcript(
        text=text,
        language=language,
        language_probability=1.0,
        no_speech_probability=no_speech,
        avg_logprob=-0.25,
    )


# 每個語言幾句直播／影片中會出現的一般句子；刻意包含和片語表相近、但不是整句相同的
# 句子（「ありがとう！」「Thank you so much.」），確認整句比對不會誤濾。
NORMAL_SENTENCES: dict[str, tuple[str, ...]] = {
    "ja": (
        "おはようございます！今日も配信始めていきます！",
        "ありがとう！めっちゃ嬉しい！",
        "んー、どうしようかな。",
        "それではまた明日の配信で会いましょう。",
    ),
    "en": (
        "Thank you so much for the super chat!",
        "So what do we do now?",
        "You can't be serious.",
        "Bye bye, see you tomorrow.",
    ),
    "zh": (
        "谢谢大家今天来看我的直播",
        "謝謝你的禮物！",
        "嗯，我觉得这个方法不错。",
        "感谢观看的各位，我们下周见。",
    ),
    "ko": (
        "감사합니다, 여러분 정말 고마워요.",
        "오늘은 새로운 게임을 해볼게요.",
        "음, 이건 좀 어렵네요.",
        "다음 주에 또 만나요!",
    ),
    "es": (
        "Muchas gracias por el apoyo.",
        "Y ahora vamos a probar otra cosa.",
        "¡Suscríbete si te gustó el directo de hoy!",
        "Chau, nos vemos mañana.",
    ),
    "fr": (
        "Merci beaucoup pour le don !",
        "C'est parti pour la deuxième partie !",
        "Je ne sais pas quoi faire maintenant.",
        "Merci à tous d'être venus ce soir.",
    ),
    "de": (
        "Vielen Dank für die Unterstützung!",
        "Das war's für heute, danke fürs Zuschauen.",
        "Ich höre gerade Musik.",
        "Bis zum nächsten Mal, ihr Lieben!",
    ),
    "pt": (
        "E aí, pessoal, tudo bem?",
        "Obrigado pela doação!",
        "O que é isso aqui no canto?",
        "Tchau, até amanhã!",
    ),
    "ru": (
        "Спасибо за подписку!",
        "Продолжение следует в следующем стриме.",
        "И что теперь делать?",
        "Сегодня мы играем в новую игру.",
    ),
    "it": (
        "Grazie mille per il supporto!",
        "Ciao a tutti, benvenuti!",
        "Buon appetito a chi sta cenando.",
        "Adesso proviamo un'altra strategia.",
    ),
    "ar": (
        "شكرا جزيلا على الدعم",
        "مرحبا بكم في البث المباشر",
        "أنا أستمع إلى الموسيقى الآن",
        "سنلعب لعبة جديدة اليوم",
    ),
    "hi": (
        "आज हम एक नया गेम खेलेंगे",
        "करती है वो हमेशा ऐसा",
        "आप सबका बहुत धन्यवाद",
        "कर दे भाई जल्दी",
    ),
    "id": (
        "Terima kasih banyak untuk donasinya!",
        "Selamat pagi semuanya!",
        "Sampai jumpa besok di stream berikutnya!",
        "Hari ini kita main game baru.",
    ),
    "vi": (
        "Cảm ơn các bạn rất nhiều!",
        "Hôm nay chúng ta sẽ chơi game mới.",
        "Hẹn gặp lại các bạn vào ngày mai.",
        "Mình không biết phải làm gì bây giờ.",
    ),
    "th": (
        "สวัสดีค่ะทุกคน วันนี้เล่นเกมใหม่",
        "ขอบคุณมากนะคะ",
        "กลับบ้านกันเถอะ",
        "โอเค ไปต่อกันเลย",
    ),
    "tr": (
        "Bağışın için çok teşekkür ederim!",
        "Abone olan herkese teşekkürler.",
        "Bugün yeni bir oyun oynayacağız.",
        "Ne yapacağımı bilmiyorum.",
    ),
    "pl": (
        "Dziękuję bardzo za wsparcie!",
        "Cześć wszystkim, witam na streamie!",
        "Dzień dobry, jak się macie?",
        "To koniec na dzisiaj.",
    ),
    "nl": (
        "Dank je wel voor de donatie!",
        "Ik luister naar muziek.",
        "Vandaag spelen we een nieuw spel.",
        "Tot morgen allemaal!",
    ),
    "uk": (
        "Дякую за підтримку!",
        "Сьогодні ми граємо в нову гру.",
        "До зустрічі наступного тижня!",
        "Я не знаю, що робити.",
    ),
}


class LexiconDataTest(unittest.TestCase):
    def test_matches_the_experiment_file(self) -> None:
        # 打包用的 Python 資料必須與實測整理出的 lexicon.json 一致（兩者一起更新）
        data = json.loads(LEXICON_JSON.read_text(encoding="utf-8"))["languages"]
        self.assertEqual(
            {
                k: {kind: tuple(v[kind]) for kind in ("hard", "soft")}
                for k, v in data.items()
            },
            {k: dict(v) for k, v in HALLUCINATION_LEXICON.items()},
        )

    def test_default_table_uses_the_lexicon_and_keeps_existing_entries(self) -> None:
        self.assertEqual(set(DEFAULT_HALLUCINATIONS), {ANY_LANGUAGE, *WHISPER_CODES})
        for language, entry in HALLUCINATION_LEXICON.items():
            for kind in ("hard", "soft"):
                with self.subTest(language=language, kind=kind):
                    expected = {normalize_for_match(p) for p in entry[kind]} - {""}
                    self.assertEqual(
                        set(CONFIG.hallucinations[language][kind]),  # type: ignore[index]
                        expected,
                    )
        # 片語表之前就有的項目都保留
        for language, kind, phrase in (
            (ANY_LANGUAGE, "hard", "Teksting av Nicolai Winther"),
            ("ja", "hard", "最後までご視聴いただきありがとうございました"),
            ("ja", "soft", "おやすみなさい"),
            ("en", "hard", "Please subscribe."),
            ("en", "soft", "you"),
            ("zh", "hard", "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目"),
            ("zh", "soft", "謝謝"),
        ):
            self.assertIn(phrase, DEFAULT_HALLUCINATIONS[language][kind])

    def test_hard_phrases_are_long_enough_and_never_soft(self) -> None:
        # hard 對所有語言生效：太短、或與某個 soft 短句相同的話會誤濾一般語音
        soft = set().union(
            *(entry["soft"] for entry in CONFIG.hallucinations.values())  # type: ignore[union-attr]
        )
        for phrase in CONFIG.hard_phrases:
            with self.subTest(phrase=phrase):
                self.assertGreaterEqual(len(phrase), 4)
                self.assertNotIn(phrase, soft)


class LexiconFilteringTest(unittest.TestCase):
    def test_every_hard_phrase_is_rejected_under_any_language(self) -> None:
        for entry in HALLUCINATION_LEXICON.values():
            for phrase in entry["hard"]:
                for language in WHISPER_CODES:
                    with self.subTest(phrase=phrase, language=language):
                        self.assertIsNotNone(rejection_reason(t(phrase, language)))

    def test_representative_hard_phrases_per_language(self) -> None:
        for phrase in (
            "ご視聴ありがとうございました",
            # vtuber_eval：large-v3 即時路徑在純 BGM 上輸出、且通過過濾的套話
            "次の動画でお会いしましょう。",
            "Thanks for watching!",
            "字幕by索兰娅",
            "다음 영상에서 만나요.",
            "Gracias por ver el video.",
            "Sous-titrage ST' 501",
            "Untertitelung des ZDF, 2020",
            "Legenda Adriana Zanotto",
            "Субтитры сделал DimaTorzok",
            "Sottotitoli e revisione a cura di QTSS",
            "اشتركوا في القناة",
            "Terima kasih sudah menonton!",
            "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
            "Altyazı M.K.",
            "Dzięki za oglądanie!",
            "TV Gelderland 2021",
            "Дякую за перегляд!",
        ):
            for language in ("ja", "en", "zh", "hi", "th"):
                with self.subTest(phrase=phrase, language=language):
                    self.assertEqual(
                        rejection_reason(t(phrase, language)), "known_hallucination"
                    )

    def test_soft_phrases_only_in_their_language_with_high_no_speech(self) -> None:
        for language, entry in HALLUCINATION_LEXICON.items():
            if language == ANY_LANGUAGE:
                continue
            for phrase in entry["soft"]:
                key = normalize_for_match(phrase)
                with self.subTest(language=language, phrase=phrase):
                    self.assertIsNotNone(
                        rejection_reason(t(phrase, language, SOFT_NO_SPEECH))
                    )
                    # no_speech 低（真人在說話）時不靠片語濾掉
                    self.assertNotEqual(
                        rejection_reason(t(phrase, language)), "known_hallucination"
                    )
                    # 其他語言的表沒有這句時，不因片語濾掉
                    for other in WHISPER_CODES:
                        if key in CONFIG.soft_phrases_for(other):
                            continue
                        self.assertNotEqual(
                            rejection_reason(t(phrase, other, SOFT_NO_SPEECH)),
                            "known_hallucination",
                            other,
                        )

    def test_normal_sentences_are_kept_under_every_language(self) -> None:
        self.assertEqual(set(NORMAL_SENTENCES), set(WHISPER_CODES))
        for own, sentences in NORMAL_SENTENCES.items():
            for text in sentences:
                with self.subTest(text=text):
                    # 自己的語言、no_speech 略低於 soft 門檻（短句的 no_speech 本來就偏高）
                    self.assertIsNone(
                        rejection_reason(
                            t(text, own, CONFIG.soft_no_speech_threshold - 0.05)
                        )
                    )
                    for language in WHISPER_CODES:
                        self.assertIsNone(rejection_reason(t(text, language)), language)

    def test_translate_benchmark_lines_are_kept(self) -> None:
        lines = json.loads(TRANSLATE_LINES.read_text(encoding="utf-8"))["lines"]
        self.assertGreater(len(lines), 0)
        for line in lines:
            for language in (line["source"], "ja", "en"):
                with self.subTest(id=line["id"], language=language):
                    self.assertIsNone(rejection_reason(t(line["text"], language)))

    @unittest.skipUnless(
        ASR_RESULTS.is_dir() and any(ASR_RESULTS.glob("*.json")),
        "experiments/asr_benchmark/results is local data (git-ignored); "
        "run experiments/asr_benchmark first to include real recognition outputs",
    )
    def test_real_japanese_recognition_outputs_are_not_known_hallucinations(
        self,
    ) -> None:
        checked = 0
        for path in sorted(ASR_RESULTS.glob("*.json")):
            for run in json.loads(path.read_text(encoding="utf-8")):
                for row in run.get("rows", []):
                    if row.get("kind") != "speech" or not row.get("text"):
                        continue
                    transcript = Transcript(
                        text=row["text"],
                        language=row.get("detected_language") or "ja",
                        language_probability=1.0,
                        no_speech_probability=float(row["no_speech"]),
                        avg_logprob=float(row["avg_logprob"]),
                    )
                    checked += 1
                    with self.subTest(file=path.name, clip=row.get("clip")):
                        self.assertNotEqual(
                            rejection_reason(transcript), "known_hallucination"
                        )
        self.assertGreater(checked, 0)


class CombiningMarksTest(unittest.TestCase):
    """印地語、泰語的母音與聲調記號（Mn／Mc）是字的一部分，去掉會讓不同的詞相同。"""

    def test_devanagari_and_thai_marks_are_kept(self) -> None:
        for a, b in (
            ("झाल", "झल"),
            ("करते हैं", "करती है"),
            ("कर दो", "कर दे"),
            ("กลับ", "กลบ"),
            ("ขอบคุณครับ", "ขอบคณครบ"),
        ):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(normalize_for_match(a), normalize_for_match(b))
        self.assertTrue(
            any(unicodedata.category(ch) == "Mn" for ch in normalize_for_match("करते हैं"))
        )

    def test_other_scripts_still_ignore_marks(self) -> None:
        # 阿拉伯文的母音符號（harakat）可有可無，Whisper 的輸出時有時無
        self.assertEqual(normalize_for_match("شُكْرًا"), normalize_for_match("شكرا"))
        # 組合用的重音符號經 NFKC 合成成單一字母，照樣比對得到
        self.assertEqual(normalize_for_match("Café"), normalize_for_match("Café"))

    def test_hindi_soft_phrase_no_longer_matches_a_different_word(self) -> None:
        self.assertEqual(
            rejection_reason(t("करते हैं", "hi", SOFT_NO_SPEECH)),
            "known_hallucination",
        )
        self.assertIsNone(rejection_reason(t("करती है", "hi", SOFT_NO_SPEECH)))


if __name__ == "__main__":
    unittest.main()
