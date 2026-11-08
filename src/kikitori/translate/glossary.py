"""翻譯術語表：內建的日語直播用語、中翻日的中日同形異義詞，加上使用者自訂詞條。

位於 translate 模組的資料層：翻譯器（`client.py`）每句呼叫 `Glossary.lookup()`，只把
「這句原文實際出現的詞」交給 `prompts.build_messages()` 放進提示詞。為什麼需要：
experiments/translate_eval 中連 Qwen3.5 9B 都把 赤スパ 譯成「紅色辣椒粉」、推し 譯成
「我喜歡的人」、メン限 譯成 "men's lineup"；HY-MT 7B 更把 スパチャ 音譯成「斯帕查」。
WMT 的術語任務與 HY-MT 官方的術語干預範本都是「出現才給」的做法，提示詞短、不會讓模型
把沒出現的詞硬塞進譯文。

內建表只收直播社群通用、譯法可靠的詞；有平台官方名稱者用官方名稱（YouTube 繁中介面
的 Super Chat 是「超級留言」、會員是「頻道會員」）。沒有可靠譯法的語言就不給，讓模型
自己翻。容易和一般用語撞字的詞只收不會誤判的寫法（例如不收單獨的「草」「箱」「枠」，
只收「草生え」「箱推し」「歌枠」），寧可少給也不要在一般句子裡誤導模型。
資料寫在 Python 裡：打包（PyInstaller）會自動收進來，不必另外列資料檔。

中翻日另有一張內建表（`ZH_JA_FALSE_FRIENDS`）：中文與日文共用漢字，模型會用日文的讀法
斷詞或理解同形詞——Qwen3.5 的 tokenizer 把「豬排便當」切成 豬｜排便｜當，譯成
「豚の排泄物」；「節目」「階段」「顏色」在日文是「轉折點」「樓梯」「臉色」。做法同上
（出現才給，最長比對），依據是字典輔助提示的研究（chain-of-dictionary，EMNLP 2024；
IdiomKB，AAAI 2024）與 WMT23 術語任務。複合詞條目同時告訴模型詞的邊界。只收中文意思
穩定、而且日文同形詞意思不同的詞；不收單字（「娘」在「姑娘」「新娘」裡意思不同），
有歧義的寫法以較長的寫法擋住（「老婆婆」不是「老婆」）。
"""

import re
import threading
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

MAX_TERMS_PER_REQUEST = 8
"""一句最多放幾條術語：一句字幕很少同時出現更多直播用語，提示詞也不該比原文長太多。"""
MAX_USER_ENTRIES = 500
MAX_SOURCE_CHARS = 100
MAX_TARGET_CHARS = 200
BUILTIN_SOURCE_LANGUAGE = "ja"
"""直播用語表只適用於日語原文（Whisper 語言碼）。"""
BUILTIN_TARGET_LANGUAGES: tuple[str, ...] = ("zh-TW", "zh-CN", "en", "ko")


@dataclass(frozen=True)
class Term:
    """要放進提示詞的一條術語：原文寫法 → 譯法。"""

    source: str
    target: str


@dataclass(frozen=True)
class BuiltinTerm:
    """內建表的一個詞條。

    Attributes:
        sources: 原文寫法（第一個是代表寫法，其餘是常見變體）。
        targets: 譯文語言碼 → 譯法；沒有可靠譯法的語言不列。
    """

    sources: tuple[str, ...]
    targets: Mapping[str, str]


def _term(sources: tuple[str, ...], **targets: str) -> BuiltinTerm:
    # 關鍵字參數不能有連字號，所以用 zh_TW 寫，這裡轉回清單語言碼。
    return BuiltinTerm(
        sources=sources,
        targets=MappingProxyType({k.replace("_", "-"): v for k, v in targets.items()}),
    )


# 譯法依據：YouTube 各語系介面的功能名稱（Super Chat／超級留言／超级留言／슈퍼챗、
# Super Sticker、頻道會員、會員專屬）；其餘為中文、英文、韓文 VTuber 社群的通用說法
# （聯動、切片、歌回、斗內、業配、collab、clipper、최애、ㅋㅋㅋ）。
BUILTIN_TERMS: tuple[BuiltinTerm, ...] = (
    # --- YouTube 功能 ---------------------------------------------------------
    _term(
        ("スパチャ", "スーパーチャット"),
        zh_TW="超級留言",
        zh_CN="超级留言",
        en="Super Chat",
        ko="슈퍼챗",
    ),
    _term(
        ("赤スパ", "赤スパチャ"),
        zh_TW="紅色超級留言",
        zh_CN="红色超级留言",
        en="red Super Chat",
        ko="빨간 슈퍼챗",
    ),
    _term(
        ("スパチャ読み",),
        zh_TW="唸超級留言",
        zh_CN="读超级留言",
        en="Super Chat reading",
        ko="슈퍼챗 읽기",
    ),
    _term(
        ("スーパーステッカー",),
        zh_TW="超級貼圖",
        zh_CN="超级贴纸",
        en="Super Sticker",
        ko="슈퍼 스티커",
    ),
    _term(("投げ銭",), zh_TW="斗內", zh_CN="打赏", en="tip", ko="후원"),
    _term(
        ("メンバーシップ",),
        zh_TW="頻道會員",
        zh_CN="频道会员",
        en="channel membership",
        ko="채널 멤버십",
    ),
    _term(
        ("メン限", "メンバー限定"),
        zh_TW="會員專屬",
        zh_CN="会员专属",
        en="members-only",
        ko="멤버십 전용",
    ),
    _term(
        ("チャンネル登録",),
        zh_TW="訂閱頻道",
        zh_CN="订阅频道",
        en="subscribe",
        ko="구독",
    ),
    _term(
        ("チャンネル登録者", "登録者数"),
        zh_TW="頻道訂閱人數",
        zh_CN="频道订阅人数",
        en="subscribers",
        ko="구독자 수",
    ),
    _term(("高評価",), zh_TW="按讚", zh_CN="点赞", en="like", ko="좋아요"),
    _term(("低評価",), zh_TW="倒讚", zh_CN="点踩", en="dislike", ko="싫어요"),
    _term(
        ("収益化",),
        zh_TW="營利化",
        zh_CN="开通收益",
        en="monetization",
        ko="수익 창출",
    ),
    _term(
        ("アーカイブ", "アーカイヴ"),
        zh_TW="直播存檔",
        zh_CN="直播回放",
        en="archive",
        ko="다시보기",
    ),
    _term(
        ("同接", "同時接続", "同時接続数"),
        zh_TW="同時觀看人數",
        zh_CN="同时观看人数",
        en="concurrent viewers",
        ko="동시 시청자 수",
    ),
    # --- 直播的種類 -------------------------------------------------------------
    _term(("配信", "生配信"), zh_TW="直播", zh_CN="直播", en="stream", ko="방송"),
    _term(("生放送",), zh_TW="直播", zh_CN="直播", en="livestream", ko="생방송"),
    _term(("配信者",), zh_TW="實況主", zh_CN="主播", en="streamer", ko="스트리머"),
    _term(
        ("初配信",),
        zh_TW="初次直播",
        zh_CN="首次直播",
        en="debut stream",
        ko="첫 방송",
    ),
    _term(("歌枠",), zh_TW="歌回", zh_CN="歌回", en="karaoke stream", ko="노래 방송"),
    _term(
        ("雑談枠", "雑談配信"),
        zh_TW="雜談直播",
        zh_CN="杂谈直播",
        en="chatting stream",
        ko="잡담 방송",
    ),
    _term(
        ("耐久配信", "耐久枠"),
        zh_TW="耐久直播",
        zh_CN="耐久直播",
        en="endurance stream",
        ko="내구 방송",
    ),
    _term(
        ("ゲリラ配信",),
        zh_TW="突襲直播",
        zh_CN="突击直播",
        en="surprise stream",
        ko="게릴라 방송",
    ),
    _term(
        ("同時視聴",),
        zh_TW="同步觀看",
        zh_CN="同步观看",
        en="watchalong",
        ko="같이보기",
    ),
    _term(
        ("案件配信",),
        zh_TW="業配直播",
        zh_CN="商单直播",
        en="sponsored stream",
        ko="협찬 방송",
    ),
    _term(("凸待ち",), zh_TW="等人連線", zh_CN="等人连线", en="call-in stream"),
    _term(("待機所",), zh_TW="待機室", zh_CN="待机室", en="waiting room", ko="대기실"),
    _term(("正座待機",), zh_TW="正坐等待", zh_CN="正坐等待", en="waiting eagerly"),
    _term(
        ("コラボ", "コラボ配信"), zh_TW="聯動", zh_CN="联动", en="collab", ko="콜라보"
    ),
    _term(
        ("オフコラボ",),
        zh_TW="線下聯動",
        zh_CN="线下联动",
        en="offline collab",
        ko="오프라인 콜라보",
    ),
    _term(
        ("3Dお披露目",),
        zh_TW="3D首次亮相",
        zh_CN="3D首次亮相",
        en="3D debut",
        ko="3D 데뷔",
    ),
    _term(("新衣装",), zh_TW="新衣裝", zh_CN="新衣装", en="new outfit", ko="신의상"),
    _term(("歌ってみた",), zh_TW="翻唱", zh_CN="翻唱", en="song cover", ko="커버곡"),
    _term(
        ("オリ曲",),
        zh_TW="原創曲",
        zh_CN="原创曲",
        en="original song",
        ko="오리지널 곡",
    ),
    _term(
        ("マシュマロ読み",),
        zh_TW="讀棉花糖匿名提問",
        zh_CN="读棉花糖匿名提问",
        en="reading anonymous Marshmallow questions",
    ),
    # --- 剪輯與觀眾 -------------------------------------------------------------
    _term(("切り抜き",), zh_TW="精華剪輯", zh_CN="切片", en="clip", ko="클립"),
    _term(("切り抜き師",), zh_TW="剪輯師", zh_CN="切片作者", en="clipper"),
    _term(("リスナー",), zh_TW="觀眾", zh_CN="观众", en="viewers", ko="시청자"),
    _term(
        ("初見さん",),
        zh_TW="第一次來的觀眾",
        zh_CN="第一次来的观众",
        en="first-time viewer",
        ko="처음 오신 분",
    ),
    _term(
        ("初見プレイ",),
        zh_TW="首次遊玩",
        zh_CN="首次游玩",
        en="blind playthrough",
        ko="첫 플레이",
    ),
    _term(("推し",), zh_TW="推", zh_CN="推", en="oshi", ko="최애"),
    _term(("最推し",), zh_TW="最推", zh_CN="最推", en="top oshi", ko="최애"),
    _term(("箱推し",), zh_TW="箱推", zh_CN="箱推", en="fan of the whole agency"),
    _term(("ガチ恋", "ガチ恋勢"), zh_TW="真愛粉", zh_CN="真爱粉", en="gachikoi"),
    _term(("指示厨",), en="backseater"),
    # --- VTuber 的身分 ----------------------------------------------------------
    _term(
        ("企業勢",),
        zh_TW="企業勢",
        zh_CN="企业势",
        en="corporate VTuber",
        ko="기업세",
    ),
    _term(("個人勢",), zh_TW="個人勢", zh_CN="个人势", en="indie VTuber", ko="개인세"),
    _term(
        ("中の人",), zh_TW="中之人", zh_CN="中之人", en="the person behind the avatar"
    ),
    _term(("卒業",), zh_TW="畢業", zh_CN="毕业", en="graduation", ko="졸업"),
    # --- 反應 -----------------------------------------------------------------
    _term(
        ("草生え", "大草原", "草不可避"),
        zh_TW="笑死",
        zh_CN="笑死",
        en="lol",
        ko="ㅋㅋㅋ",
    ),
    _term(("www",), zh_TW="哈哈哈", zh_CN="哈哈哈", en="lol", ko="ㅋㅋㅋ"),
    _term(("てぇてぇ", "てえてえ"), zh_TW="太尊了", zh_CN="太尊了", en="so precious"),
    # 8888 是拍手聲（パチパチ）；中文的 8888 是「掰掰」，最容易被誤譯。
    _term(("8888",), zh_TW="（鼓掌）", zh_CN="（鼓掌）", en="*clap clap*", ko="짝짝짝"),
    _term(
        ("おつです", "おつでした"),
        zh_TW="辛苦了",
        zh_CN="辛苦了",
        en="good work",
        ko="수고하셨어요",
    ),
)


# 中文原文 → 日文：中日同形異義詞與斷詞陷阱。譯法是日文的一般說法；同一個詞的繁簡
# 寫法都列（Whisper 可能輸出任一種）。較長的寫法放在同一張表裡，最長比對會先用它們，
# 擋住短詞的誤判（「老婆婆」「佔便宜」「汽車站」「大丈夫」）。
ZH_JA_FALSE_FRIENDS: tuple[BuiltinTerm, ...] = (
    # --- 斷詞陷阱：不整個比對，模型會切成別的詞 ---------------------------------
    _term(("豬排便當", "猪排便当"), ja="豚カツ弁当"),
    _term(("豬排", "猪排"), ja="豚カツ"),
    _term(("便當", "便当"), ja="弁当"),
    # --- 家人、人物 ------------------------------------------------------------
    _term(("老婆",), ja="妻"),
    _term(("老婆婆",), ja="おばあさん"),
    _term(("丈夫",), ja="夫"),
    # 台灣網路上「大丈夫」是日語借詞（沒問題），不能被「丈夫」（先生）吃掉
    _term(("大丈夫",), ja="大丈夫"),
    _term(("大家",), ja="みんな"),
    _term(("老師", "老师"), ja="先生"),
    _term(("經理", "经理"), ja="マネージャー"),
    # --- 日常用語 --------------------------------------------------------------
    _term(("新聞", "新闻"), ja="ニュース"),
    _term(("節目", "节目"), ja="番組"),
    _term(("汽車", "汽车"), ja="車"),
    _term(("汽車站", "汽车站"), ja="バスターミナル"),
    _term(("汽水",), ja="炭酸飲料"),
    _term(("手紙", "手纸"), ja="トイレットペーパー"),
    _term(("走路",), ja="歩く"),
    _term(("工作",), ja="仕事"),
    _term(("便宜",), ja="安い"),
    _term(("佔便宜", "占便宜"), ja="得をする"),
    _term(("顏色", "颜色"), ja="色"),
    _term(("麻雀",), ja="スズメ"),
    _term(("看病",), ja="診察を受ける"),
    _term(("階段", "阶段"), ja="段階"),
    # --- 動作、感受 ------------------------------------------------------------
    _term(("結束", "结束"), ja="終わる"),
    _term(("告訴", "告诉"), ja="教える"),
    _term(("放心",), ja="安心する"),
    _term(("小心",), ja="気をつけて"),
    _term(("迷惑",), ja="戸惑う"),
    _term(("約束", "约束"), ja="束縛する"),
    _term(("質問", "质问"), ja="問い詰める"),
    _term(("分手",), ja="別れる"),
    _term(("感人",), ja="感動的"),
    _term(("有意思",), ja="面白い"),
    _term(("沒意思", "没意思"), ja="つまらない"),
    _term(("愛面子", "爱面子"), ja="メンツを気にする"),
    _term(("面子",), ja="メンツ"),
    _term(("心中",), ja="心の中"),
    _term(("印象深刻",), ja="印象に残る"),
)
_BUILTIN_TABLES: Mapping[str, tuple[BuiltinTerm, ...]] = MappingProxyType(
    {BUILTIN_SOURCE_LANGUAGE: BUILTIN_TERMS, "zh": ZH_JA_FALSE_FRIENDS}
)
"""原文語言（Whisper 語言碼）→ 內建表；譯文語言由每個詞條的 `targets` 決定。"""


@dataclass(frozen=True)
class GlossaryEntry:
    """使用者自訂的一個詞條（設定檔 `translation.glossary.entries` 的一筆）。

    Attributes:
        source: 原文寫法（比對時不分大小寫與全半形）。
        target: 譯法。
        target_language: 只用於這個譯文語言（清單語言碼）；None 表示所有譯文語言。
            語言碼由呼叫端（設定驗證）以語言清單正規化。

    Raises:
        TypeError: 欄位型別不對。
        ValueError: 空字串、太長、含控制字元。
    """

    source: str
    target: str
    target_language: str | None = None

    def __post_init__(self) -> None:
        _check_text("source", self.source, MAX_SOURCE_CHARS)
        _check_text("target", self.target, MAX_TARGET_CHARS)
        if self.target_language is not None and not isinstance(
            self.target_language, str
        ):
            raise TypeError("target_language must be a string or None")
        if not match_key(self.source):
            raise ValueError("source must contain at least one visible character")


def _check_text(name: str, value: object, limit: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not start or end with whitespace")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    if not value.isprintable():
        raise ValueError(f"{name} must not contain control characters or line breaks")


def match_key(text: str) -> str:
    """比對用的正規化：NFKC（全形英數、半形片假名統一）＋ casefold（不分大小寫）。

    ASR 輸出的寫法不固定（ｗｗｗ／www、ｽﾊﾟﾁｬ／スパチャ），使用者輸入也是。
    """
    return unicodedata.normalize("NFKC", text).casefold().strip()


@dataclass(frozen=True)
class _Index:
    pattern: re.Pattern[str] | None
    terms: Mapping[str, Term]


@dataclass
class Glossary:
    """一個 session 的術語表：內建表（可關閉）＋使用者詞條。

    使用者詞條優先於內建表（同一個原文寫法時用使用者的譯法）；指定譯文語言的使用者詞條
    優先於不指定的。查詢結果依譯文語言快取，可被多個執行緒同時使用（快取競爭時最多
    重算一次，結果相同）。

    Attributes:
        entries: 使用者詞條。
        builtin: 是否使用內建表（日語直播用語、中翻日的中日同形異義詞）。
    """

    entries: Sequence[GlossaryEntry] = ()
    builtin: bool = True
    _cache: dict[tuple[str, str], _Index] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def lookup(
        self, text: str, source_language: str, target_language: str
    ) -> tuple[Term, ...]:
        """這句原文出現的術語（依出現順序、不重複，最多 `MAX_TERMS_PER_REQUEST` 條）。

        比對以 `match_key()` 正規化後的文字進行，同一位置取最長的寫法（「スパチャ読み」
        優先於「スパチャ」），找到的範圍不重疊。

        Args:
            text: 原文。
            source_language: 原文的 Whisper 語言碼（內建表只有 "ja" 與 "zh"）。
            target_language: 譯文的清單語言碼。
        """
        index = self._index(source_language, target_language)
        if index.pattern is None:
            return ()
        found: dict[Term, None] = {}
        for match in index.pattern.finditer(match_key(text)):
            found.setdefault(index.terms[match.group(0)], None)
            if len(found) >= MAX_TERMS_PER_REQUEST:
                break
        return tuple(found)

    def _index(self, source_language: str, target_language: str) -> _Index:
        key = (source_language, target_language)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        terms: dict[str, Term] = {}
        if self.builtin:
            for item in _BUILTIN_TABLES.get(source_language, ()):
                target = item.targets.get(target_language)
                if target is None:
                    continue
                for source in item.sources:
                    terms[match_key(source)] = Term(source, target)
        # 不指定語言的先放、指定語言的後放：後放的覆蓋同一個寫法。
        for entry in _ordered(self.entries, target_language):
            terms[match_key(entry.source)] = Term(entry.source, entry.target)
        index = _Index(pattern=_compile(terms), terms=MappingProxyType(terms))
        with self._lock:
            self._cache[key] = index
        return index


def _ordered(
    entries: Iterable[GlossaryEntry], target_language: str
) -> list[GlossaryEntry]:
    general = [e for e in entries if e.target_language is None]
    specific = [e for e in entries if e.target_language == target_language]
    return general + specific


def _compile(terms: Mapping[str, Term]) -> re.Pattern[str] | None:
    if not terms:
        return None
    # 長的寫法排前面：regex 的「或」在同一位置取第一個符合的，就是最長的那個。
    keys = sorted(terms, key=lambda k: (-len(k), k))
    return re.compile("|".join(re.escape(k) for k in keys))


def builtin_entry_count() -> int:
    """內建表的詞條數（文件與測試用）。"""
    return len(BUILTIN_TERMS)
