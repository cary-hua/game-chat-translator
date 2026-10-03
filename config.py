"""配置读写：程序旁边的 config.json。

所有文件 IO 都显式指定 encoding="utf-8"。中文 Windows 的默认编码是 GBK，
不指定会把中文提示词写坏，或者直接抛 UnicodeEncodeError。
"""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _resource_dir() -> Path:
    """随程序分发的只读文件（图标之类）在哪。

    打包成 exe 后它们躺在 PyInstaller 的解压目录里（sys._MEIPASS），
    跟"程序自己所在的文件夹"是两码事，别混用。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def _writable(folder: Path) -> bool:
    probe = folder / ".write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _pick_data_dir() -> Path:
    """配置、历史记录、截图写哪儿。

    ⚠️ 不能直接用 __file__ 的目录：打包成 exe 后它指向 PyInstaller 的临时解压
    目录，每次运行都不一样、退出就删 —— 配置写进去等于没存，用户填的 key
    重启就没了。

    优先 exe 旁边（解压即用，绿色软件该有的样子）；那里写不进去（比如被放在
    Program Files）就退到 %APPDATA%，别让人开都开不了。
    """
    here = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    if _writable(here):
        return here

    fallback = Path(os.environ.get("APPDATA") or Path.home()) / "game-translator"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        return here  # 连这儿都不行，那就还写原地，让报错正常浮上来
    return fallback


PROJECT_DIR = _pick_data_dir()   # 可写的：配置、历史、截图
RESOURCE_DIR = _resource_dir()   # 只读的：图标等随包资源
CONFIG_PATH = PROJECT_DIR / "config.json"

ENV_API_KEY = "GCT_API_KEY"

# 提示词固定不变 —— 这样每次请求的前缀完全相同，能吃到 DeepSeek 的上下文缓存，
# 输入成本降到 1/50。里面不要插入任何每次都会变的内容（时间戳、随机数）。
#
# 要求返回 JSON 而不是纯文本，是为了拿到 source（原文）：
# 客户端要靠原文去重，不能靠译文 —— 同一句 hello 这次翻"你好"下次翻"您好"，
# 用译文当标识会把同一条消息判成两条。
DEFAULT_PROMPT = """你是游戏聊天翻译助手。图片是一块游戏聊天框的截图。

规则：
1. 识别图中所有可见的文字，按从上到下的顺序。
2. 非中文文本翻译成简体中文；已经是中文的行，translation 照抄原文即可。
3. 忽略 UI 元素：按钮、输入提示（如 "Press Enter to chat"）、时间戳、系统公告。
4. 以下词汇在中文玩家中也耳熟能详，请保持英文原样，不要翻译：{keep_english}
5. 以下词汇按术语表翻译：{glossary}
6. 术语表里用「/」分隔多个译法的词（比如 ad 写成「物理攻击/射手」），
   表示它有多种含义 —— 请结合上下文挑最合适的那个；实在拿不准就保留原文。
7. {name_rule}
8. 图中没有可翻译文本时，输出 []。

只输出 JSON 数组。不要 markdown 代码块，不要任何解释文字：
[{"user": "说话人，没有就填空字符串", "source": "识别到的原始文字", "translation": "简体中文"}]"""

# 历史版本的默认提示词。检测到配置里存的还是这些，就静默升级成新版 ——
# 否则老用户的 config.json 会一直用旧提示词，新功能等于没生效。
# 用户自己改过的提示词不在此列，不会被覆盖。
_LEGACY_PROMPTS = (
    """你是游戏聊天翻译助手。图片是一块游戏聊天框的截图。

规则：
1. 识别图中所有可见的文字，保持原有的行顺序。
2. 非中文文本翻译成简体中文；已经是中文的行原样保留。
3. 每行格式：[说话人] 译文 —— 没有说话人就只输出译文。
4. 忽略 UI 元素：按钮、输入提示（如 "Press Enter to chat"）、时间戳、系统公告。
5. 严格按术语表翻译：{glossary}
6. 只输出翻译结果。不要解释，不要 markdown 代码块，不要加引号。
7. 图中没有可翻译文本时，只输出：<空>""",
    # JSON 版，但还没有「保持英文」那条规则
    """你是游戏聊天翻译助手。图片是一块游戏聊天框的截图。

规则：
1. 识别图中所有可见的文字，按从上到下的顺序。
2. 非中文文本翻译成简体中文；已经是中文的行，translation 照抄原文即可。
3. 忽略 UI 元素：按钮、输入提示（如 "Press Enter to chat"）、时间戳、系统公告。
4. 严格按术语表翻译：{glossary}
5. 图中没有可翻译文本时，输出 []。

只输出 JSON 数组。不要 markdown 代码块，不要任何解释文字：
[{"user": "说话人，没有就填空字符串", "source": "识别到的原始文字", "translation": "简体中文"}]""",
    # 有「保持英文」规则，但还没有多义词说明
    """你是游戏聊天翻译助手。图片是一块游戏聊天框的截图。

规则：
1. 识别图中所有可见的文字，按从上到下的顺序。
2. 非中文文本翻译成简体中文；已经是中文的行，translation 照抄原文即可。
3. 忽略 UI 元素：按钮、输入提示（如 "Press Enter to chat"）、时间戳、系统公告。
4. 以下词汇在中文玩家中也耳熟能详，请保持英文原样，不要翻译：{keep_english}
5. 以下词汇按术语表翻译：{glossary}
6. 图中没有可翻译文本时，输出 []。

只输出 JSON 数组。不要 markdown 代码块，不要任何解释文字：
[{"user": "说话人，没有就填空字符串", "source": "识别到的原始文字", "translation": "简体中文"}]""",
)


# 默认分类是个**空**的 —— 不预设任何游戏。
# 这工具是给所有带聊天框的游戏的，默认替用户选一款游戏（还塞七十来条术语）
# 等于先替人家做了决定。想用现成的那份，自己切到「英雄联盟」就行。
DEFAULT_GLOSSARY_NAME = "默认"
LOL_GLOSSARY_NAME = "英雄联盟"

# 日服专用那批。日文句子模型直接就能翻，术语表装的是
# 「约定俗成的固定说法」—— 直译会走味，或者根本翻不出那个意思。
DEFAULT_GLOSSARY_JP = [
    {"term": "おつ", "translation": "辛苦了", "translate": True},
    {"term": "お疲れ", "translation": "辛苦了", "translate": True},
    {"term": "乙", "translation": "辛苦了", "translate": True},
    {"term": "よろ", "translation": "请多指教", "translate": True},
    {"term": "よろしく", "translation": "请多指教", "translate": True},
    {"term": "ナイス", "translation": "漂亮", "translate": True},
    {"term": "うまい", "translation": "厉害", "translate": True},
    {"term": "うますぎ", "translation": "太强了", "translate": True},
    {"term": "草", "translation": "笑", "translate": True},
    {"term": "ワロタ", "translation": "笑死", "translate": True},
    {"term": "キル", "translation": "击杀", "translate": True},
    {"term": "デス", "translation": "阵亡", "translate": True},
    {"term": "アシスト", "translation": "助攻", "translate": True},
    {"term": "サレンダー", "translation": "投降", "translate": True},
    {"term": "集まれ", "translation": "集合", "translate": True},
    {"term": "逃げて", "translation": "快跑", "translate": True},
    {"term": "危ない", "translation": "小心", "translate": True},
    {"term": "上手い", "translation": "打得好", "translate": True},
    {"term": "下手", "translation": "打得差", "translate": True},
    {"term": "すみません", "translation": "抱歉", "translate": True},
    {"term": "ごめん", "translation": "抱歉", "translate": True},
    {"term": "ありがとう", "translation": "谢谢", "translate": True},
    {"term": "どうも", "translation": "谢了", "translate": True},
]


def default_glossaries() -> list:
    """默认给两个分类：一个空的「默认」，一个配好的「英雄联盟」。

    空的那个是默认选中的 —— 不预设你玩什么游戏。术语表本来就是
    「某个游戏的约定俗成说法」，没玩那款游戏的人拿着一堆 gg/おつ
    没用，甚至会干扰模型的判断。想用现成的那份，切过去就行。

    分类是按【游戏】分的，不是按服务器或语言 ——
    日服 LOL 和国服 LOL 是同一个游戏，术语表就该是一份：
    英文缩写和日文说法放在一起。
    """
    entries = [dict(e) for e in DEFAULT_GLOSSARY]
    have = {e["term"] for e in entries}
    for extra in DEFAULT_GLOSSARY_JP:
        if extra["term"] not in have:
            entries.append(dict(extra))
            have.add(extra["term"])

    return [
        {"name": DEFAULT_GLOSSARY_NAME, "entries": []},
        {"name": LOL_GLOSSARY_NAME, "entries": entries},
    ]


def _upgrade_glossaries(pd: dict) -> tuple[list, str]:
    """把各种历史配置统一成 (分类列表, 当前分类名)。

    走过的格式：
      · glossary = {...}                  （最老的 dict）
      · glossary = {...} + keep_english   （dict + 单独的保持英文列表）
      · glossary = [{...}, ...]           （条目列表，还没有分类）
      · glossaries = [{name, entries}]    （现在这样）
    """
    # 已经是分类格式
    raw_groups = pd.get("glossaries")
    if isinstance(raw_groups, list) and raw_groups:
        groups = []
        for g in raw_groups:
            if not isinstance(g, dict):
                continue
            name = str(g.get("name") or "").strip()
            if not name:
                continue
            entries = _upgrade_glossary(g.get("entries"))
            groups.append({"name": name, "entries": entries})
        if groups:
            active = str(pd.get("active_glossary") or "").strip()
            names = [g["name"] for g in groups]
            if active not in names:
                active = names[0]
            return groups, active

    # 老格式：把 glossary 收成一个分类。那会儿的术语表就是照 LOL 填的，
    # 所以归到「英雄联盟」名下，别塞进空的「默认」里。
    entries = _upgrade_glossary(pd.get("glossary"), pd.get("keep_english"))
    return (
        [{"name": LOL_GLOSSARY_NAME, "entries": entries}],
        LOL_GLOSSARY_NAME,
    )


def _upgrade_glossary(raw, keep_english=None) -> list:
    """把历史上出现过的几种术语表格式，统一成条目列表。

    走过的格式：
      1. dict            —— {"ty": "谢谢", "gg": "打得不错"}
      2. dict + 单独列表  —— 上面那个再加 keep_english=["gg","rush"]
      3. 条目列表（现在）  —— [{"term":..., "translation":..., "translate": bool}]
    """
    keep = {str(w).strip().lower() for w in (keep_english or []) if str(w).strip()}

    # 已经是新格式
    if isinstance(raw, list):
        entries = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term") or "").strip()
            if not term:
                continue
            translation = str(item.get("translation") or "").strip() or term
            entries.append({
                "term": term,
                "translation": translation,
                "translate": bool(item.get("translate", True)),
            })
        # ⚠️ 空的就返回空的，别拿默认条目去填。
        # 用户完全可能故意建一个空分类、准备自己一条条加；
        # 填成 27 条 LOL 词，他下次打开会以为自己点错了什么。
        return entries

    # 旧格式（dict）
    if isinstance(raw, dict) and raw:
        # 最老的默认表（gg 翻成"打得不错"）—— 语义已经变了，直接换新默认
        if raw.get("gg") == "打得不错" and raw.get("rush") == "快攻":
            return [dict(e) for e in DEFAULT_GLOSSARY]

        entries = []
        seen = set()
        for term, translation in raw.items():
            term = str(term or "").strip()
            if not term:
                continue
            seen.add(term.lower())
            entries.append({
                "term": term,
                "translation": str(translation or term).strip(),
                # 在 keep_english 名单里的 -> 不翻
                "translate": term.lower() not in keep,
            })

        # ⚠️ keep_english 里有些词并不在 glossary 里（比如 lol、afk），
        # 只遍历 glossary 会把它们整个丢掉。这里补回来。
        for word in (keep_english or []):
            word = str(word or "").strip()
            if word and word.lower() not in seen:
                entries.append({
                    "term": word,
                    "translation": word,
                    "translate": False,
                })

        return entries or [dict(e) for e in DEFAULT_GLOSSARY]

    return [dict(e) for e in DEFAULT_GLOSSARY]


def _upgrade_prompt(prompt: str) -> str:
    """老版本默认提示词就升级成新的；用户自己改过的原样保留。"""
    if not prompt:
        return DEFAULT_PROMPT
    stripped = prompt.strip()
    for legacy in _LEGACY_PROMPTS:
        if stripped == legacy.strip():
            return DEFAULT_PROMPT
    return prompt

DEFAULT_HOTKEY = "<ctrl>+<alt>+t"
DEFAULT_HOTKEY_REPLY = "<ctrl>+<alt>+e"
DEFAULT_HOTKEY_INLINE = "<ctrl>+<alt>+i"
DEFAULT_HOTKEY_RESELECT = "<ctrl>+<alt>+r"
DEFAULT_HOTKEY_WINDOW = "<ctrl>+<alt>+h"
DEFAULT_HOTKEY_QUIT = "<ctrl>+<alt>+q"

# 界面上能改的热键：(Profile 字段名, 显示名, 默认组合键)，顺序即界面顺序。
#
# 以前只有主翻译键能改，其余写死在代码里 —— 但既然主键能换，
# 别的键没有理由不能换（游戏热键冲突、跟别的软件撞车，都是常事）。
HOTKEY_FIELDS = [
    ("hotkey", "翻译聊天栏（看别人说啥）", DEFAULT_HOTKEY),
    ("hotkey_reply", "弹小框说中文（翻好自动发出）", DEFAULT_HOTKEY_REPLY),
    ("hotkey_inline", "游戏里打了中文，就地翻掉", DEFAULT_HOTKEY_INLINE),
    ("hotkey_reselect", "重新框选区域", DEFAULT_HOTKEY_RESELECT),
    ("hotkey_window", "显示 / 隐藏主窗口", DEFAULT_HOTKEY_WINDOW),
    ("hotkey_quit", "退出程序", DEFAULT_HOTKEY_QUIT),
]

HOTKEY_DEFAULTS = {field: default for field, _name, default in HOTKEY_FIELDS}
HOTKEY_LABELS = {field: name for field, name, _default in HOTKEY_FIELDS}

# 你说话时翻成什么语言。日服选日文，欧美服选英文。
# 只列常用的几个，用户能自己填别的（模型认语言名）。
REPLY_LANGS = ["日文", "英文", "韩文", "繁体中文"]
DEFAULT_REPLY_LANG = "日文"

# 发送按键的默认序列。
#
#   小框那条：回车开聊天框 → 把译文逐字符打进去 → 回车发出
#   游戏内那条：退格清空原来打的中文 → 逐字符打 → 回车发出
#
# 为什么不用 Ctrl+V 粘贴：实测 LOL 收单键（回车进得去），但不收 Ctrl 组合键 ——
# Ctrl+V 粘不进去、Ctrl+A 也选不中。序列里写 `text` 就是改用逐字符 Unicode
# 输入，不碰剪贴板也不按任何修饰键。
DEFAULT_SEND_SEQ_QUICK = "enter,text,enter"
DEFAULT_SEND_SEQ_INLINE = "backspace*100,text,enter"

# 这两个是历史默认值。老配置里存着它们的话静默换上新默认 ——
# 不改的话，用户在配置里存的旧序列会一直生效，新默认等于没写。
# 用户自己改过的序列（不在这两个列表里）不动。
_LEGACY_SEND_SEQS = {
    "send_seq_quick": {"enter,ctrl+v,enter"},
    "send_seq_inline": {"ctrl+a,ctrl+v,enter"},
}
_DEFAULT_SEND_SEQS = {
    "send_seq_quick": DEFAULT_SEND_SEQ_QUICK,
    "send_seq_inline": DEFAULT_SEND_SEQ_INLINE,
}


def _upgrade_send_seq(raw, field: str) -> str:
    """取发送序列：空着用默认，是旧默认值就换新的，用户改过的照旧。"""
    value = str(raw or "").strip()
    if not value or value in _LEGACY_SEND_SEQS.get(field, ()):
        return _DEFAULT_SEND_SEQS[field]
    return value


SEND_MODES = ("auto", "confirm", "clipboard")


def _upgrade_send_mode(pd: dict) -> str:
    """取「翻完怎么办」，并接管历史上那两个布尔字段。

    走过的格式：
      · auto_send: bool        —— true=直接发 / false=只放剪贴板
      · confirm_send: bool     —— 上个版本的「发送前先看一眼」
      · send_mode: str         —— 现在这个（三选一）

    按字段本来的语义映射：`auto_send=false` 是"只复制"，
    `confirm_send=true` 是"先看一眼"。两者都有的话优先认新字段。
    """
    mode = str(pd.get("send_mode") or "").strip()
    if mode in SEND_MODES:
        return mode
    if "confirm_send" in pd:
        return "confirm" if pd["confirm_send"] else "auto"
    if "auto_send" in pd:
        return "auto" if pd["auto_send"] else "clipboard"
    return "auto"

# 默认术语表：一张表管两种情况，每条自带「翻不翻」开关。
#   translate=True  -> 翻成 translation
#   translate=False -> 保持英文原样不动
# 用户可以随便增删改。
DEFAULT_GLOSSARY = [
    # ── 保持英文（中文玩家也认识，翻了反而累赘）──
    {"term": "gg", "translation": "打得不错", "translate": False},
    {"term": "ez", "translation": "轻松拿下", "translate": False},
    {"term": "afk", "translation": "暂离", "translate": False},
    {"term": "omg", "translation": "天啊", "translate": False},
    {"term": "rush", "translation": "快攻", "translate": False},
    {"term": "adc", "translation": "射手", "translate": False},
    {"term": "op", "translation": "太强了", "translate": False},
    {"term": "cd", "translation": "冷却", "translate": False},
    {"term": "tp", "translation": "传送", "translate": False},
    {"term": "buff", "translation": "增强", "translate": False},
    {"term": "gank", "translation": "抓人", "translate": False},

    # ── 需要翻译（国服玩家不太熟，翻出来才看得懂）──
    {"term": "nerf", "translation": "削弱", "translate": True},
    {"term": "ult", "translation": "大招", "translate": True},
    {"term": "mp", "translation": "蓝量", "translate": True},
    {"term": "hp", "translation": "血量", "translate": True},
    {"term": "jg", "translation": "打野", "translate": True},
    {"term": "sup", "translation": "辅助", "translate": True},
    {"term": "top", "translation": "上路", "translate": True},
    {"term": "mid", "translation": "中路", "translate": True},
    {"term": "def", "translation": "防守", "translate": True},
    {"term": "push", "translation": "推线", "translate": True},
    {"term": "feed", "translation": "送人头", "translate": True},
    {"term": "camp", "translation": "蹲点", "translate": True},
    {"term": "brb", "translation": "马上回来", "translate": True},
    {"term": "lol", "translation": "哈哈", "translate": True},
    {"term": "wp", "translation": "打得好", "translate": True},
    {"term": "ty", "translation": "谢谢", "translate": True},
    {"term": "nt", "translation": "打得好，别灰心", "translate": True},
    {"term": "mb", "translation": "我的错", "translate": True},
    {"term": "my bad", "translation": "我的错", "translate": True},
    {"term": "ns", "translation": "好枪", "translate": True},
    {"term": "gj", "translation": "干得好", "translate": True},
    {"term": "hf", "translation": "玩得开心", "translate": True},
    {"term": "gl hf", "translation": "祝好运，玩得开心", "translate": True},
    {"term": "sry", "translation": "抱歉", "translate": True},
    {"term": "thx", "translation": "谢谢", "translate": True},
    {"term": "np", "translation": "没事", "translate": True},
    {"term": "nub", "translation": "菜鸟", "translate": True},
    {"term": "smurf", "translation": "小号", "translate": True},
    {"term": "int", "translation": "故意送人头", "translate": True},
    {"term": "flank", "translation": "绕后", "translate": True},
    {"term": "peel", "translation": "保护后排", "translate": True},
    {"term": "kite", "translation": "风筝", "translate": True},
    {"term": "poke", "translation": "消耗", "translate": True},
    {"term": "thresh", "translation": "锤石", "translate": True},
    {"term": "ad", "translation": "物理攻击/射手", "translate": True},
    {"term": "bot", "translation": "下路/人机", "translate": True},
    {"term": "solo", "translation": "单挑/一人一线", "translate": True},
]


@dataclass
class Rect:
    """屏幕区域。

    坐标是虚拟桌面的物理像素绝对坐标，支持负值（左侧副屏 left 会是负的）。
    不存"相对某块屏"，多屏场景下才能重建正确位置。
    """

    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    def as_mss_dict(self) -> dict:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }

    def is_valid(self) -> bool:
        return self.width >= 20 and self.height >= 10

    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)


@dataclass
class OverlayConfig:
    mode: str = "side"  # side | cover | corner
    alpha: float = 0.88
    font_family: str = "Microsoft YaHei UI"
    font_size: int = 12
    # 宽度按译文长短自动算，夹在这两个值之间：短句用窄框，长句用宽框
    min_width: int = 280
    max_width: int = 760
    max_height: int = 440
    corner: str = "bottom-right"  # corner 模式下贴哪个角
    auto_hide_sec: float = 20.0
    anchor_gap: int = 8  # side 模式与框选区域之间的间距
    last_pos: list | None = None  # 用户拖动后的位置 [x, y]


@dataclass
class CaptureConfig:
    jpeg_quality: int = 85
    upscale: float = 1.0  # 小字体游戏设 2.0，明显提升识别率
    max_edge: int = 1600  # 安全上限，超过则等比缩小


@dataclass
class Profile:
    name: str = "default"
    rect: Rect | None = None
    prompt: str = DEFAULT_PROMPT
    # 术语表分类：[{"name": 分类名, "entries": [条目...]}]
    # 每条是 {"term": 词汇, "translation": 译文, "translate": 是否翻译}。
    #
    # 同一时间只用一个分类（active_glossary 指的那个）——
    # 同一个词在不同游戏里意思可能完全不同，两套同时生效会打架。
    glossaries: list = field(default_factory=default_glossaries)
    active_glossary: str = DEFAULT_GLOSSARY_NAME
    hotkey: str = DEFAULT_HOTKEY
    # 其余五个热键。以前是写死的常量，现在都能在界面上改 ——
    # 主翻译键能被游戏抢，别的键一样会跟别的软件撞车。
    hotkey_reply: str = DEFAULT_HOTKEY_REPLY
    hotkey_inline: str = DEFAULT_HOTKEY_INLINE
    hotkey_reselect: str = DEFAULT_HOTKEY_RESELECT
    hotkey_window: str = DEFAULT_HOTKEY_WINDOW
    hotkey_quit: str = DEFAULT_HOTKEY_QUIT
    model: str = "deepseek-flash"
    max_tokens: int = 1024
    temperature: float = 0.2
    exe_names: list = field(default_factory=list)  # 以后按前台进程名自动切换

    # 回话时翻成什么语言。日服选「日文」，欧美服选「英文」。
    # 和 prompt 是两回事：prompt 管「看别人说什么」，这个管「你说什么」。
    reply_lang: str = DEFAULT_REPLY_LANG

    # 「在游戏里打中文、按热键就地翻译」用的区域：聊天输入框那一行。
    # 和 rect（聊天栏）分开存 —— 输入框在聊天栏下方，是另一块地方。
    input_rect: Rect | None = None

    # 监视模式：不按热键，隔一会儿自己看一眼聊天栏，有新消息就翻出来。
    #
    # ⚠️ **只在"聊天框不透明、底下画面不动"的游戏上成立**。实测数据：
    #   不透明：画面没变 0.000 / 来新消息 0.775~1.629 —— 分得干干净净
    #   半透明（LOL）：背景平移 1.3~1.7 / 来新消息 0.34 —— 背景比新消息还大
    # 后半种情况下判据会一直误报，每条"新消息"都白花一次 token。所以默认关。
    #
    # 它省 token 全靠 skip_unchanged，开监视时会自动把那个也打开。
    watch_mode: bool = False
    watch_interval_ms: int = 1200

    # 翻完之后怎么办。三选一：
    #   "auto"      —— 直接发进游戏（默认，最省事）
    #   "confirm"   —— 先把译文摆回小框，你再按一次回车才真发。
    #                  用来拦模型偶尔翻错、或者多写一串的情况。
    #   "clipboard" —— 只放剪贴板，你自己粘。
    #                  **游戏支持粘贴时才走得通** —— LOL 有意禁了外部粘贴，
    #                  在它那儿这条路是堵的，别的游戏多半没问题。
    #
    # （"confirm" 只管小框那条路。游戏里打字那条翻完直接发 —— 那时候聊天框
    #  还开着，弹窗抢焦点可能把里面已经打的字弄丢。）
    send_mode: str = "auto"
    # 发按键的序列：逗号分隔动作。三种写法：
    #   组合键        ctrl+v
    #   逐字符输入    text        （把译文一个字符一个字符打进去）
    #   重复 N 次     backspace*100
    # 换游戏按键不一样时改这里（有的游戏用 T 开聊天框，就换成 "t,text,enter"）。
    send_seq_quick: str = DEFAULT_SEND_SEQ_QUICK
    send_seq_inline: str = DEFAULT_SEND_SEQ_INLINE


@dataclass
class Config:
    version: int = 1
    api_key: str = ""  # repr 里会露出来，日志统一走 redact()
    api_base: str = "https://api.deepseek.com"
    provider: str = "deepseek"
    active_profile: str = "default"
    profiles: dict = field(default_factory=dict)
    # 画面和上次几乎一样就跳过调用（省 token）。
    # **默认关**：它是"整块画面像素没变才跳过"，而聊天框多半是半透明的
    # （LOL 就是），底下画面一直在动 —— 判据会频繁误判成"有新消息"，
    # 等于每次白比对一遍，省不下什么还多一层不确定性。想省再打开。
    skip_unchanged: bool = False
    # 说话人的名字要不要也翻译。
    # 默认关 —— 名字多半是 ID、梗、谐音或者随便打的字符串，
    # 硬翻出来往往莫名其妙。想看名字含义时再打开。
    translate_names: bool = False
    # 热键的工作方式，由浅到深：
    #   "register" —— RegisterHotKey，正规无副作用。但游戏用 DirectInput
    #                 抢了底层键盘 IO 时会被整个拦掉，LOL 就是。
    #   "poll"     —— GetAsyncKeyState 轮询，不注册不挂钩子（默认）。
    #                 没有副作用，对游戏的兼容性明显好于标准方式。
    #   "hook"     —— 低级键盘钩子 WH_KEYBOARD_LL，挂在系统键盘消息链上，
    #                 比游戏更靠前。AutoHotkey 就是靠它进游戏的；
    #                 但也正是安全软件眼里的 keylogger 特征，最后再试它。
    hotkey_mode: str = "poll"
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)

    def profile(self) -> Profile:
        """当前生效的 profile，拿不到就给个默认的。"""
        p = self.profiles.get(self.active_profile)
        if p is None:
            p = Profile()
            self.profiles[self.active_profile] = p
        return p


def default_config() -> Config:
    return Config(profiles={"default": Profile()})


def _filter_fields(cls, raw: dict) -> dict:
    """只保留 dataclass 认识的字段，配置里多了东西也不炸。"""
    known = cls.__dataclass_fields__
    return {k: v for k, v in (raw or {}).items() if k in known}


def config_from_dict(raw: dict) -> Config:
    profiles = {}
    for name, pd in (raw.get("profiles") or {}).items():
        pd = pd or {}
        rect_raw = pd.get("rect")
        input_raw = pd.get("input_rect")
        glossaries, active_glossary = _upgrade_glossaries(pd)
        profiles[name] = Profile(
            name=pd.get("name", name),
            rect=Rect(**_filter_fields(Rect, rect_raw)) if rect_raw else None,
            prompt=_upgrade_prompt(pd.get("prompt", DEFAULT_PROMPT)),
            glossaries=glossaries,
            active_glossary=active_glossary,
            hotkey=pd.get("hotkey") or DEFAULT_HOTKEY,
            hotkey_reply=pd.get("hotkey_reply") or DEFAULT_HOTKEY_REPLY,
            hotkey_inline=pd.get("hotkey_inline") or DEFAULT_HOTKEY_INLINE,
            hotkey_reselect=pd.get("hotkey_reselect") or DEFAULT_HOTKEY_RESELECT,
            hotkey_window=pd.get("hotkey_window") or DEFAULT_HOTKEY_WINDOW,
            hotkey_quit=pd.get("hotkey_quit") or DEFAULT_HOTKEY_QUIT,
            model=pd.get("model", "deepseek-flash"),
            max_tokens=pd.get("max_tokens", 1024),
            temperature=pd.get("temperature", 0.2),
            exe_names=pd.get("exe_names") or [],
            reply_lang=pd.get("reply_lang") or DEFAULT_REPLY_LANG,
            input_rect=(
                Rect(**_filter_fields(Rect, input_raw)) if input_raw else None
            ),
            watch_mode=bool(pd.get("watch_mode", False)),
            watch_interval_ms=int(pd.get("watch_interval_ms", 1200) or 1200),
            send_mode=_upgrade_send_mode(pd),
            send_seq_quick=_upgrade_send_seq(pd.get("send_seq_quick"), "send_seq_quick"),
            send_seq_inline=_upgrade_send_seq(pd.get("send_seq_inline"), "send_seq_inline"),
        )
    if not profiles:
        profiles = {"default": Profile()}

    return Config(
        version=raw.get("version", 1),
        api_key=raw.get("api_key", ""),
        api_base=raw.get("api_base", "https://api.deepseek.com"),
        provider=raw.get("provider", "deepseek"),
        active_profile=raw.get("active_profile", "default"),
        profiles=profiles,
        skip_unchanged=raw.get("skip_unchanged", False),
        translate_names=raw.get("translate_names", False),
        hotkey_mode=raw.get("hotkey_mode") or "poll",
        overlay=OverlayConfig(**_filter_fields(OverlayConfig, raw.get("overlay"))),
        capture=CaptureConfig(**_filter_fields(CaptureConfig, raw.get("capture"))),
    )


class ConfigStore:
    """配置的读写。写在项目目录里，用户好找好改。"""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = Path(path)

    def load(self) -> Config:
        if not self.path.exists():
            cfg = default_config()
            self.save(cfg)
            return cfg
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 配置损坏：备份一份再从默认重建，不让程序起不来
            backup = self.path.with_name(self.path.name + ".bak")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            cfg = default_config()
            self.save(cfg)
            return cfg
        return config_from_dict(raw)

    def save(self, cfg: Config) -> None:
        """原子写：先写临时文件再 replace。

        悬浮窗位置这类保存很频繁，进程若在中途被杀，直接覆写会留下半截 JSON。
        """
        data = asdict(cfg)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)


def resolve_api_key(cfg: Config) -> str:
    """优先级：环境变量 GCT_API_KEY > 配置文件。"""
    return os.environ.get(ENV_API_KEY) or cfg.api_key or ""


def redact(text: str) -> str:
    """日志脱敏：任何可能含 key 的字符串都先过这里。"""
    if not text:
        return text
    return text if len(text) <= 8 else text[:4] + "…" + text[-2:]
