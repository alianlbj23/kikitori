"""Whisper 在非語音上的已知幻覺片語，依 Whisper 語言碼分表（`asr.filtering` 的資料）。

位於 asr 模組的資料層：`filtering.DEFAULT_HALLUCINATIONS` 由這份表建立。內容是
`experiments/hallucination_lexicon/lexicon.json`（2026-11-05 實測，方法與限制見該目錄的
README）的逐字複製：small／large-v3-turbo／large-v3 對強制 19 個語言、確定沒有人聲的
音訊（MUSAN 噪音與無人聲音樂、數位靜音、白／粉紅噪音、合成音效）的輸出，人工分成
`hard`（字幕署名、片尾套話，不論辨識語言都濾）與 `soft`（真人也可能單獨說的短句，
只在同語言且 no_speech 偏高時濾）。英文另參考 Barański et al.（ICASSP 2025）。

為什麼是 Python 資料而不是 JSON 資源：PyInstaller 會自動收進匯入的模組，資料檔則要在
`packaging/engine/build.py` 另外列出，漏列時打包版會靜默地沒有片語表。更新時改
`lexicon.json` 後重新產生這個檔案（保持兩者一致），不要只改其中一個。

中文片語同時列簡體與繁體（繁體為人工轉寫）；印地語、泰語沒有找到可確定是套話的句子，
全部放 soft。片語是可讀形式，比對前經 `filtering.normalize_for_match()` 正規化。
"""

from collections.abc import Mapping
from typing import Final

LEXICON_SOURCE: Final = (
    "experiments/hallucination_lexicon (kikitori): Whisper small / "
    "large-v3-turbo / large-v3 (CTranslate2, CUDA float16), app FAST_DECODE "
    "and FINAL_DECODE, forced language, on MUSAN noise + MUSAN music "
    "without vocals (CC BY 4.0, Snyder et al. 2015), digital silence, "
    "white/pink noise and synthetic SFX bursts, 1-10 s segments. English "
    "additionally follows Barański et al., ICASSP 2025 (arXiv 2501.11378). "
    "Existing entries of kikitori.asr.filtering are kept. hard = drop on "
    "exact (normalize_for_match) whole-utterance match; soft = drop only on "
    "exact match when no_speech > soft_no_speech_threshold."
)
"""片語表的來源與分類原則（英文，與 `lexicon.json` 的 `source` 相同）。"""

LEXICON_GENERATED: Final = "2026-11-05"

HALLUCINATION_LEXICON: Final[Mapping[str, Mapping[str, tuple[str, ...]]]] = {
    "*": {
        "hard": (
            "Subtitles by the Amara.org community",
            "字幕由Amara.org社区提供",
            "字幕由Amara.org社區提供",
            "Teksting av Nicolai Winther",
        ),
        "soft": (),
    },
    "ja": {
        "hard": (
            "ご視聴ありがとうございました",
            "ご清聴ありがとうございました",
            "チャンネル登録よろしくお願いします",
            "チャンネル登録お願いします",
            "最後までご視聴頂きありがとうございました",
            "最後までご視聴いただきありがとうございました",
            "チャンネル登録をお願いいたします。",
        ),
        "soft": (
            "ありがとうございました",
            "おやすみなさい",
            "ん",
            "んん",
            "んんん",
            "んんんん",
            "それではまた。",
        ),
    },
    "en": {
        "hard": (
            "Thanks for watching!",
            "Thank you for watching.",
            "Please subscribe.",
        ),
        "soft": (
            "Thank you.",
            "Thanks.",
            "you",
            "so",
            "Bye.",
            "Hmm. Hmm.",
        ),
    },
    "zh": {
        "hard": (
            "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
            "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目",
            "谢谢观看",
            "謝謝觀看",
            "感谢观看",
            "感謝觀看",
            "优优独播剧场——YoYo Television Series Exclusive",
            "優優獨播劇場——YoYo Television Series Exclusive",
            "字幕by索兰娅",
            "字幕by索蘭婭",
            "字幕志愿者 杨茜茜",
            "字幕志願者 楊茜茜",
            "中文字幕志愿者 杨茜茜",
            "中文字幕志願者 楊茜茜",
            "字幕志愿者 李宗盛",
            "字幕志願者 李宗盛",
            "中文字幕——YK",
            "中文字幕君好",
            "by bwd6",
            "MING PAO CANADA MING PAO TORONTO",
            "MING PAO CANADA MANGA",
            "中文字幕提供",
            "中文字幕 李宗盛",
        ),
        "soft": (
            "谢谢",
            "謝謝",
            "嗯嗯",
            "嗯嗯嗯",
            "啊啊啊",
            "啊啊啊啊",
        ),
    },
    "ko": {
        "hard": (
            "다음 영상에서 만나요.",
            "시청해주셔서 감사합니다.",
            "구독과 좋아요 부탁드려요!",
            "한글자막 by 한효정",
            "자막 제공 및 자막 제공 및 광고를 포함하고 있습니다.",
            "자막 제공 배달의민족",
        ),
        "soft": (
            "감사합니다.",
            "아멘",
            "음... 음...",
            "음 음 음",
            "음 음 음 음",
            "아 아 아 아 아",
        ),
    },
    "es": {
        "hard": (
            "Gracias por ver el video.",
            "¡Suscríbete al canal!",
            "Subtítulos por la comunidad de Amara.org",
            "Subtítulos realizados por la comunidad de Amara.org",
            "Subtítulos en español de Amara.org",
        ),
        "soft": (
            "Gracias.",
            "y",
            "y y y y",
            "¡Suscríbete!",
            "Chau.",
            "¡Ahhh!",
        ),
    },
    "fr": {
        "hard": (
            "Sous-titrage ST' 501",
            "Sous-titres réalisés par l'Amara.org",
            "Sous-titres réalisés par la communauté des Amara.org",
            "Sous-titres réalisés par la communauté d'Amara.org",
            "Sous-titrage Société Radio-Canada",
            "Sous-titrage FR ?",
            "– Sous-titrage FR 2021",
            "Sous-titres par Jérémy Diaz",
            "Merci d'avoir regardé cette vidéo !",
            "Abonnez vous ... Ma seconde chaîne ... Voir une autre vidéo ...",
        ),
        "soft": (
            "Merci.",
            "Merci à tous.",
            "C'est parti !",
            "Abonnez-vous !",
        ),
    },
    "de": {
        "hard": (
            "Untertitelung des ZDF, 2020",
            "Untertitelung. BR 2018",
            "Untertitel im Auftrag des ZDF für funk, 2017",
            "Untertitel im Auftrag des ZDF, 2017",
            "Copyright WDR 2020",
            "Copyright WDR 2021",
            "Vertraue und glaube, es hilft, es heilt die göttliche Kraft!",
            "Untertitel im Auftrag des ZDF",
        ),
        "soft": (
            "Vielen Dank.",
            "Bis zum nächsten Mal.",
            "Musik",
            "Musik Musik Musik Musik",
            "Amen.",
            "Tschüss.",
            "Das war's für heute.",
        ),
    },
    "pt": {
        "hard": (
            "Legenda Adriana Zanotto",
            "Legenda por Sônia Ruberti",
        ),
        "soft": (
            "E aí",
            "E aí E aí",
            "E aí E aí E aí",
            "E aí E aí E aí E aí",
            "Obrigado.",
            "Amém.",
            "O que é isso?",
            "Hum... Hum...",
            "Tchau.",
            "Tchau, tchau.",
        ),
    },
    "ru": {
        "hard": (
            "Субтитры сделал DimaTorzok",
            "Субтитры создавал DimaTorzok",
            "Субтитры подогнал «Симон»",
            "Субтитры субтитров Н.Новикова",
            "Субтитры субтитров А.Синецкая Корректор А.Егорова",
            "Редактор субтитров Н.Закомолдина Корректор А.Егорова",
            "Редактор субтитров Н.Новикова Корректор В.Сухиашвили",
            "Редактор субтитров Н.Новикова Корректор А.Егорова",
            "Редактор субтитров О.Голубкина Корректор А.Егорова",
            "Субтитры субтитры субтитры",
            "Смотрите продолжение в следующей серии.",
        ),
        "soft": (
            "Продолжение следует...",
            "Спасибо.",
            "и",
        ),
    },
    "it": {
        "hard": (
            "Sottotitoli a cura di QTSS.",
            "Sottotitoli e revisione a cura di QTSS",
            "Grazie per la visione!",
        ),
        "soft": (
            "Grazie.",
            "Grazie a tutti.",
            "Buon appetito!",
            "Ciao!",
        ),
    },
    "ar": {
        "hard": (
            "اشتركوا في القناة",
            "اشترك في القناة",
            "شكرا للمشاهدة",
            "ترجمة نانسي قنقر",
            "المترجم للقناة",
        ),
        "soft": (
            "مرحباً",
            "موسيقى",
            "شكرا",
            "شكراً لكم",
        ),
    },
    "hi": {
        "hard": (),
        "soft": (
            "झाल",
            "झाल झाल",
            "कर दो",
            "कर दो कर दो",
            "प्रवाद कर दो",
            "करते हैं",
            "आप आप आप आप",
            "आप आप आप आप आप",
            "आप आप आप आप आप आप",
            "प्रस्तुति प्रस्तुति",
            "हुआ हुआ",
            "अजय को",
            "कर दो कर दो कर दो",
            "कर दो कर दो कर दो कर दो",
            "प्रश्न प्रश्न",
        ),
    },
    "id": {
        "hard": (
            "Terima kasih telah menonton!",
            "Terima kasih sudah menonton!",
            "Terima kasih kerana menonton!",
            "sub indo by broth3rmax",
        ),
        "soft": (
            "Terima kasih.",
            "selamat menikmati",
            "Sampai jumpa!",
        ),
    },
    "vi": {
        "hard": (
            "Hãy subscribe cho kênh La La School Để không bỏ lỡ những video hấp dẫn",
            "Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn",
            "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
            "Hẹn gặp lại các bạn trong những video tiếp theo.",
            "Các bạn hãy đăng ký kênh để ủng hộ kênh của mình nhé.",
            "Hãy đăng ký kênh để ủng hộ kênh của mình nhé.",
        ),
        "soft": (),
    },
    "th": {
        "hard": (),
        "soft": (
            "กลับกลับ",
            "กลับกลับกลับ",
            "สวัสดี",
            "สวัสดีครับ",
            "สวัสดีครับทุกคน",
            "ขอบคุณครับ",
            "เฮ้",
            "เฮ้ย",
            "โอ้ โอ้ โอ้",
            "ที่สุดที่สุด",
            "เจ้าไว้",
            "โปรดติดตามตอนต่อไป",
            "โปรดติดตามต่อไป",
            "บรรยายธรรม",
            "โอ้โห",
            "โอ้โห โอ้โห โอ้โห",
            "โอ้ โอ้ โอ้ โอ้",
            "โอเค โอเค โอเค",
        ),
    },
    "tr": {
        "hard": (
            "Altyazı M.K.",
            "İzlediğiniz için teşekkür ederim.",
            "Abone olmayı unutmayın.",
        ),
        "soft": (
            "abone ol",
            "abone ol abone ol",
            "Fırt fırt fırt",
        ),
    },
    "pl": {
        "hard": (
            "Dziękuje za oglądanie!",
            "Dzięki za oglądanie!",
            "Zdjęcia i montaż",
            "Napisy stworzone przez społeczność Amara.org",
            "Napisy by Jacek Makarewicz",
            "© PTA & TVP",
        ),
        "soft": (
            "KONIEC!",
            "Dziękuję.",
            "Dziękuję za uwagę.",
            "Cześć!",
            "Dzień dobry! Dzień dobry!",
            "muzyka",
            "Hmm Hmm",
            "Zobacz, jak to się dzieje.",
            "Cześć, cześć!",
            "Dzień dobry!",
        ),
    },
    "nl": {
        "hard": (
            "TV Gelderland 2021",
            "ZANG EN MUZIEK",
        ),
        "soft": (
            "MUZIEK",
            "Dank u wel.",
            "Vroom! Vroom! Vroom!",
        ),
    },
    "uk": {
        "hard": (
            "Дякую за перегляд!",
            "Редактор субтитров О.Голубки Корректор В.Сухи",
            "Редактор субтитров О.Голубки Корректор В.Егорова",
        ),
        "soft": (
            "Дякую!",
            "Наступного року!",
        ),
    },
}
"""`{Whisper 語言碼 或 "*": {"hard": (...), "soft": (...)}}`；"*" 為不分語言的署名。"""
