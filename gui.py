"""主窗口：可视化控制面板。

配色用浅色 —— logo 是纯黑图形配透明背景，深色界面上会看不见。
"""

import json
import tkinter as tk
from tkinter import messagebox, ttk

import winutil
from config import (
    DEFAULT_REPLY_LANG,
    HOTKEY_DEFAULTS,
    HOTKEY_FIELDS,
    REPLY_LANGS,
    RESOURCE_DIR,
    SEND_MODES,
)
from hotkey import KEYSYM_TO_SPEC, vk_to_spec

ASSETS = RESOURCE_DIR / "assets"  # 打包后图标在解压目录里，不在 exe 旁边

# 署名。跟《逃出生天》汉化那份保持一致。
CREDIT = "如月见（YLR）制作　·　仅供学习交流，请勿用于商业用途"

# 「开聊天框 / 发送」那两个下拉的选项。
# 中文的两个只是为了让人一眼看懂，真发出去的是英文键名；单字母键中英文一样，
# 直接当键名用。
# 反推（中文 -> 键名）要显式写：enter 和 return 是同一个键的两种写法，
# 用推导的话后者会盖掉前者，序列里就冒出个不常见的 return。
KEY_ALIAS = {"enter": "回车", "return": "回车", "space": "空格"}
KEY_TO_ACTION = {"回车": "enter", "空格": "space"}
KEY_CHOICES = ["回车", "空格", "T", "Y", "U"]

# 浅色主题
BG = "#f5f5f7"
CARD = "#ffffff"
TEXT = "#1d1d1f"
MUTED = "#6e6e73"
ACCENT = "#0071e3"
OK_COLOR = "#1a8917"
ERR_COLOR = "#d93025"
WARN_COLOR = "#a86000"
BORDER = "#d2d2d7"

FONT = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

WINDOW_W = 900


def display_hotkey(spec: str) -> str:
    """'<ctrl>+<alt>+t' -> 'Ctrl + Alt + T'，给人看的形式。"""
    pretty = {
        "ctrl": "Ctrl", "control": "Ctrl",
        "alt": "Alt", "shift": "Shift", "win": "Win",
    }
    parts = []
    for raw in spec.split("+"):
        token = raw.strip().strip("<>").strip()
        if token:
            parts.append(pretty.get(token, token.upper()))
    return " + ".join(parts)


class MainWindow:
    """只管界面，业务逻辑一律回调给 app。"""

    def __init__(self, app) -> None:
        self.app = app
        self.root = app.root
        self._logo_img = None
        self._key_visible = False

        self._setup_style()
        self._build()

    # ---------------------------------------------------------- 样式

    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")  # clam 才好自定义配色
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT,
                        font=(FONT, 10))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT,
                        font=(FONT, 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=(FONT, 9))
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED,
                        font=(FONT, 9))
        style.configure("Section.TLabel", background=BG, foreground=TEXT,
                        font=(FONT, 10, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=(FONT, 13, "bold"))
        style.configure("TButton", font=(FONT, 10), padding=(10, 6))
        style.configure("Accent.TButton", font=(FONT, 10, "bold"),
                        padding=(10, 6))
        style.configure("TCheckbutton", background=BG, foreground=TEXT,
                        font=(FONT, 9))
        style.configure("TRadiobutton", background=CARD, foreground=TEXT,
                        font=(FONT, 10))
        style.configure("TEntry", fieldbackground=CARD, font=(FONT_MONO, 10))
        style.configure("TSeparator", background=BORDER)

        # Treeview 默认行高 20px，高 DPI 下字体渲染更大，字母上下会被切掉。
        # 行高和字体都得显式调，不能靠默认值。
        style.configure("Treeview",
                        rowheight=28,
                        font=(FONT, 10),
                        background=CARD,
                        fieldbackground=CARD,
                        foreground=TEXT)
        style.configure("Treeview.Heading", font=(FONT, 10))

    # ---------------------------------------------------------- 构建

    def _build(self) -> None:
        root = self.root
        root.title("游戏聊天栏翻译器 · 如月见")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.app.on_window_close)

        outer = ttk.Frame(root, padding=(18, 14, 18, 12))
        outer.pack(fill="both", expand=True)

        self._build_header(outer)

        # 左导航 + 右内容 —— 六个页面分开装，不用一路往下滚
        middle = ttk.Frame(outer)
        middle.pack(fill="both", expand=True, pady=(12, 0))

        nav = ttk.Frame(middle, style="Card.TFrame", padding=6)
        nav.pack(side="left", fill="y")

        content = ttk.Frame(middle)
        content.pack(side="left", fill="both", expand=True, padx=(12, 0))

        pages = [
            ("api", "API 密钥", self._build_api_section),
            ("region", "聊天区域", self._build_region_page),
            ("reply", "回话", self._build_reply_section),
            ("glossary", "术语表", self._build_glossary_section),
            ("history", "记录", self._build_history_page),
            ("hotkey", "热键", self._build_hotkey_section),
        ]

        self._pages: dict = {}
        self._nav_buttons: dict = {}

        for key, label, builder in pages:
            page = ttk.Frame(content, padding=(0, 4))
            builder(page)
            self._pages[key] = page

            btn = tk.Button(
                nav, text=label, anchor="w", relief="flat", bd=0,
                bg=CARD, fg=TEXT, activebackground=ACCENT,
                activeforeground="white", padx=10, pady=7,
                font=(FONT, 10), cursor="hand2", width=9,
                command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x", pady=1)
            self._nav_buttons[key] = btn

        self._build_footer(outer)
        self.show_page("api")

        self.center()

    # ---------------------------------------------------------- 头部

    def _build_header(self, parent) -> None:
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(0, 14))

        logo_path = ASSETS / "logo.png"
        if logo_path.exists():
            try:
                # 用 PIL 缩放，tk.PhotoImage 的 subsample 只能整数倍
                from PIL import Image, ImageTk

                im = Image.open(logo_path).convert("RGBA")
                target_w = 168
                if im.width > target_w:
                    ratio = target_w / im.width
                    im = im.resize(
                        (target_w, max(1, round(im.height * ratio))),
                        Image.LANCZOS,
                    )
                self._logo_img = ImageTk.PhotoImage(im)
                ttk.Label(box, image=self._logo_img, background=BG).pack()
            except Exception:
                self._logo_img = None

        ttk.Label(box, text="游戏聊天栏翻译器", style="Title.TLabel").pack(pady=(8, 0))
        ttk.Label(
            box,
            text="框选聊天栏 · 按热键 · 出中文",
            style="Muted.TLabel",
        ).pack(pady=(2, 0))

        # 权限不摆在界面上了（绝大多数人用不到，白占一行）。
        # 但排查「热键在游戏里没反应」时它是关键线索，所以照样写进启动日志，
        # 见 main._log_startup。

    # ---------------------------------------------------------- API

    def _build_api_section(self, parent) -> None:
        ttk.Label(parent, text="API Key", style="Section.TLabel").pack(anchor="w")

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(6, 0))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")

        self.api_var = tk.StringVar(value=self.app.cfg.api_key)
        self.api_entry = ttk.Entry(
            row, textvariable=self.api_var, show="•", width=34,
        )
        self.api_entry.pack(side="left", fill="x", expand=True)

        ttk.Checkbutton(
            row, text="显示", command=self._toggle_key_visible,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            card,
            text="默认用 DeepSeek 的 key，去 platform.deepseek.com 申请",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(6, 8))

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.pack(fill="x")
        self.verify_btn = ttk.Button(
            actions, text="保存并验证", style="Accent.TButton",
            command=self._on_save_key,
        )
        self.verify_btn.pack(side="left")
        self.api_hint = ttk.Label(actions, text="", style="CardMuted.TLabel")
        self.api_hint.pack(side="left", padx=(10, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        ttk.Label(card, text="换个 AI（可选）", style="Card.TLabel").pack(anchor="w")
        ttk.Label(
            card,
            text="默认的 DeepSeek 已经配好了，不管这里就行。\n"
                 "要换别家的话填下面两项 —— 两个前提：对方提供的是 OpenAI\n"
                 "兼容接口，而且你选的模型得能看图（本工具靠模型直接读截图，\n"
                 "没有 OCR 那一层）。不会填就留默认，别猜。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(2, 6))

        self.base_var = tk.StringVar(value=self.app.cfg.api_base)
        self.model_var = tk.StringVar(value=self.app.cfg.profile().model)
        for label, var in (("接口地址", self.base_var), ("模型名", self.model_var)):
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, width=9, style="CardMuted.TLabel").pack(
                side="left"
            )
            ttk.Entry(line, textvariable=var, width=44).pack(
                side="left", fill="x", expand=True
            )

    def _toggle_key_visible(self) -> None:
        self._key_visible = not self._key_visible
        self.api_entry.configure(show="" if self._key_visible else "•")

    def _on_save_key(self) -> None:
        self.app.save_api_key(
            self.api_var.get().strip(),
            self.base_var.get().strip(),
            self.model_var.get().strip(),
        )

    def set_api_hint(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.api_hint.configure(text=text, foreground=color)

    def set_verify_busy(self, busy: bool) -> None:
        self.verify_btn.configure(
            text="验证中…" if busy else "保存并验证",
            state="disabled" if busy else "normal",
        )

    # ---------------------------------------------------------- 区域

    def _build_region_page(self, parent) -> None:
        """框选区域和显示位置本来就是一回事，合在一页更好找。"""
        self._build_region_section(parent)
        self._build_overlay_section(parent)

    def _build_region_section(self, parent) -> None:
        ttk.Label(parent, text="聊天栏区域", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(6, 0))

        self.region_var = tk.StringVar()
        ttk.Label(card, textvariable=self.region_var, style="Card.TLabel").pack(
            anchor="w"
        )

        ttk.Button(
            card, text="框选区域", command=self.app.begin_select,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        # 聊天输入框 —— 只有「在游戏里打了中文再翻」那条路要用
        ttk.Label(card, text="聊天输入框（可选）", style="Card.TLabel").pack(anchor="w")

        self.input_var = tk.StringVar()
        ttk.Label(card, textvariable=self.input_var, style="Card.TLabel").pack(
            anchor="w", pady=(2, 0)
        )

        ttk.Button(
            card, text="框选输入框", command=self.app.begin_select_input,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(
            card,
            text="框住你打字的那一条（不是整个聊天栏）。\n"
                 "设好之后：在游戏里打好中文，按 Ctrl+Alt+I 就地翻成"
                 "日文替换掉，不用切窗口。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        self.skip_var = tk.BooleanVar(value=self.app.cfg.skip_unchanged)
        ttk.Checkbutton(
            card, text="画面没变就跳过翻译（省 token）",
            variable=self.skip_var, command=self._on_skip_changed,
        ).pack(anchor="w")
        ttk.Label(
            card,
            text="默认关着 —— 它是「整块画面像素没变才跳过」，而聊天框多半是\n"
                 "半透明的（LOL 就是），底下画面一直在动，判据会频繁误判成\n"
                 "「有新消息」，等于白比对一次。真想要这层节省再打开。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(2, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        # ── 监视模式：不按热键，自己盯着聊天栏 ──
        self.watch_var = tk.BooleanVar(value=self.app.cfg.profile().watch_mode)
        ttk.Checkbutton(
            card, text="监视模式：一直盯着聊天栏，有新消息自动翻（不用按热键）",
            variable=self.watch_var, command=self._on_watch_changed,
        ).pack(anchor="w")
        ttk.Label(
            card,
            text="⚠️ 只在「聊天框不透明、底下画面不跟着动」的游戏上成立。\n"
                 "半透明聊天框（LOL 就是）用不了 —— 背景一直在动，判据分不出\n"
                 "来，会一直误报、白花 token。开之前先想清楚你的聊天框是哪种。\n"
                 "打开它会顺带打开上面的「画面没变就跳过」—— 那是省钱的关键。",
            style="CardMuted.TLabel", justify="left", foreground=WARN_COLOR,
        ).pack(anchor="w", pady=(2, 4))

        watch_row = ttk.Frame(card, style="Card.TFrame")
        watch_row.pack(fill="x")
        ttk.Label(watch_row, text="多久看一眼", style="CardMuted.TLabel").pack(
            side="left"
        )
        self.watch_interval_var = tk.StringVar(
            value=f"{self.app.cfg.profile().watch_interval_ms / 1000:.1f}"
        )
        ttk.Combobox(
            watch_row, textvariable=self.watch_interval_var, width=5,
            values=["0.8", "1.0", "1.2", "1.5", "2.0", "3.0"],
        ).pack(side="left", padx=(6, 0))
        ttk.Label(watch_row, text="秒", style="CardMuted.TLabel").pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(
            watch_row, text="应用", width=6, command=self._on_watch_interval,
        ).pack(side="left", padx=(10, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        self.names_var = tk.BooleanVar(value=self.app.cfg.translate_names)
        ttk.Checkbutton(
            card, text="翻译说话人的名字",
            variable=self.names_var, command=self._on_names_changed,
        ).pack(anchor="w")
        ttk.Label(
            card,
            text="名字多半是 ID、梗、谐音或者随便打的字符串，翻出来未必有意义，\n"
                 "所以默认关着。想看日语名或外语 ID 是什么意思时再打开。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(2, 0))

        self.refresh_region()

    def _on_skip_changed(self) -> None:
        self.app.set_skip_unchanged(self.skip_var.get())

    def set_skip_var(self, value: bool) -> None:
        """同步那个勾选框 —— 开监视模式时会替用户把它打开。"""
        try:
            self.skip_var.set(bool(value))
        except tk.TclError:
            pass

    def _on_watch_changed(self) -> None:
        self.app.set_watch(self.watch_var.get())

    def _on_watch_interval(self) -> None:
        try:
            secs = float(self.watch_interval_var.get())
        except ValueError:
            self.set_status("间隔得填个数字，比如 1.2", "error")
            return
        if secs < 0.6:
            self.set_status("太快了 —— 最少 0.6 秒", "error")
            return
        self.app.set_watch(self.watch_var.get(), int(secs * 1000))

    def _on_names_changed(self) -> None:
        self.app.set_translate_names(self.names_var.get())

    def refresh_region(self) -> None:
        profile = self.app.cfg.profile()

        rect = profile.rect
        if rect is None:
            self.region_var.set("尚未设置 —— 点下面的按钮框住游戏聊天栏")
        else:
            self.region_var.set(
                f"✔ 已设置   位置 ({rect.left}, {rect.top})   大小 {rect.width} × {rect.height}"
            )

        inrect = profile.input_rect
        if inrect is None:
            self.input_var.set("尚未设置 —— 设了才能用 Ctrl+Alt+I")
        else:
            self.input_var.set(
                f"✔ 已设置   位置 ({inrect.left}, {inrect.top})   "
                f"大小 {inrect.width} × {inrect.height}"
            )

    # ---------------------------------------------------------- 悬浮窗

    def _build_overlay_section(self, parent) -> None:
        ttk.Label(parent, text="翻译显示在哪里", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
        card.pack(fill="x", pady=(6, 0))

        self.mode_var = tk.StringVar(value=self.app.cfg.overlay.mode)
        options = [
            ("side", "贴在聊天栏旁边", "不遮挡原文"),
            ("cover", "覆盖在原位", "省地方，看不到原文"),
            ("corner", "固定在屏幕角落", "像字幕"),
        ]
        for value, label, hint in options:
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Radiobutton(
                line, text=label, value=value, variable=self.mode_var,
                command=self._on_mode_change,
            ).pack(side="left")
            ttk.Label(line, text=hint, style="CardMuted.TLabel").pack(
                side="left", padx=(10, 0)
            )

    def _on_mode_change(self) -> None:
        self.app.set_overlay_mode(self.mode_var.get())

    # ---------------------------------------------------------- 热键

    def _build_reply_section(self, parent) -> None:
        ttk.Label(parent, text="说给外国人听", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(6, 0))

        # 翻成什么语言 —— 日服选日文，欧美服选英文
        lang_row = ttk.Frame(card, style="Card.TFrame")
        lang_row.pack(fill="x", pady=(0, 6))
        ttk.Label(lang_row, text="翻成：", style="CardMuted.TLabel").pack(side="left")

        profile = self.app.cfg.profile()
        self.reply_lang_var = tk.StringVar(value=profile.reply_lang or DEFAULT_REPLY_LANG)
        lang_box = ttk.Combobox(
            lang_row, textvariable=self.reply_lang_var, values=REPLY_LANGS,
            width=10, state="readonly",
        )
        lang_box.pack(side="left")
        lang_box.bind("<<ComboboxSelected>>", lambda _e: self._on_reply_lang())
        ttk.Label(
            lang_row, text="（日服打日文，欧美服打英文）", style="CardMuted.TLabel",
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            card,
            text="打中文，翻成对方语言，直接发进游戏。\n"
                 "游戏里按 Ctrl+Alt+E 就能弹出小框，不用切过来。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(0, 6))

        self.reply_input = tk.Text(
            card, height=2, width=42, wrap="word", font=(FONT, 10),
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            padx=4, pady=3,
        )
        self.reply_input.pack(fill="x")

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))

        self.reply_btn = ttk.Button(
            row, text=self._reply_btn_text(), command=self._on_reply,
        )
        self.reply_btn.pack(side="left")

        self.reply_hint = ttk.Label(row, text="", style="CardMuted.TLabel")
        self.reply_hint.pack(side="left", padx=(10, 0))

        self.reply_out = ttk.Label(
            card, text="", style="CardMuted.TLabel",
            wraplength=400, justify="left",
        )
        self.reply_out.pack(anchor="w", pady=(6, 0))

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        # ── 翻完怎么办：三档，别只剩"自动发"一条路 ──
        ttk.Label(card, text="翻完怎么办", style="Card.TLabel").pack(anchor="w")
        self.send_mode_var = tk.StringVar(value=profile.send_mode)
        for value, label, hint in (
            ("auto", "直接发进游戏", "默认，最省事"),
            ("confirm", "先摆出来给我看一眼", "确认了才发，能拦下模型翻错"),
            ("clipboard", "只放剪贴板", "我自己粘 —— 游戏支持粘贴时才走得通"),
        ):
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Radiobutton(
                line, text=label, value=value, variable=self.send_mode_var,
                command=self._on_send_mode_changed,
            ).pack(side="left")
            ttk.Label(line, text=hint, style="CardMuted.TLabel").pack(
                side="left", padx=(10, 0)
            )

        ttk.Separator(card).pack(fill="x", pady=(10, 8))

        # ── 发送按键：先给两个键的下拉，序列自动生成，不用用户懂语法 ──
        ttk.Label(card, text="发送按键（按你的游戏改）", style="Card.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            card,
            text="程序靠「发按键」来开聊天框、打字、发送，所以得知道你的游戏用哪\n"
                 "个键。下面是绝大多数游戏的默认值，不是的话改这两个下拉就行。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(2, 6))

        self.chat_key_var = tk.StringVar()
        self.send_key_var = tk.StringVar()
        for label, var, note in (
            ("开聊天框", self.chat_key_var, "大多游戏是回车；有的用 T、Y、U"),
            ("发送", self.send_key_var, "几乎都是回车"),
        ):
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, width=8, style="CardMuted.TLabel").pack(
                side="left"
            )
            combo = ttk.Combobox(line, textvariable=var, width=8, values=KEY_CHOICES)
            combo.pack(side="left")
            combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_key_choices())
            ttk.Label(line, text=note, style="CardMuted.TLabel").pack(
                side="left", padx=(10, 0)
            )

        self._load_key_choices(profile)

        ttk.Label(
            card,
            text="高级 —— 直接改按键序列（上面两个下拉就是改这个，一般不用动）",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(8, 3))

        self.seq_quick_var = tk.StringVar(value=profile.send_seq_quick)
        self.seq_inline_var = tk.StringVar(value=profile.send_seq_inline)
        for label, var, hint in (
            ("小框这条", self.seq_quick_var, "开聊天框 → 打字 → 发送"),
            ("游戏内这条", self.seq_inline_var, "清空原文 → 打字 → 发送"),
        ):
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=label, width=10, style="CardMuted.TLabel").pack(
                side="left"
            )
            ttk.Entry(line, textvariable=var, width=26).pack(side="left")
            ttk.Label(line, text=hint, style="CardMuted.TLabel").pack(
                side="left", padx=(8, 0)
            )

        ttk.Label(
            card,
            text="写法：逗号分隔动作，+ 连组合键。text = 把译文逐字符打进去；\n"
                 "backspace*100 = 重复 100 次。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(4, 0))

        seq_row = ttk.Frame(card, style="Card.TFrame")
        seq_row.pack(fill="x", pady=(6, 0))
        ttk.Button(
            seq_row, text="保存发送按键", command=self._on_save_seqs,
        ).pack(side="left")
        self.seq_hint = ttk.Label(seq_row, text="", style="CardMuted.TLabel")
        self.seq_hint.pack(side="left", padx=(10, 0))

        ttk.Label(
            card,
            text="⚠️ 开聊天框那个键要是填错了，用回话功能就不是「消息发不出去」，\n"
                 "而是往游戏里乱按一通。不确定就把上面选成「只放剪贴板」。",
            style="CardMuted.TLabel", justify="left", foreground=WARN_COLOR,
        ).pack(anchor="w", pady=(8, 0))

    # ---------------------------------------------------- 回话：发送方式

    def _on_send_mode_changed(self) -> None:
        profile = self.app.cfg.profile()
        mode = self.send_mode_var.get()
        if mode not in SEND_MODES:
            return
        profile.send_mode = mode
        try:
            self.app.store.save(self.app.cfg)
        except OSError as exc:
            self.set_reply_hint(f"保存失败：{exc}", "error")
            return
        self.set_reply_hint({
            "auto": "✔ 翻完直接发进游戏",
            "confirm": "✔ 翻完先摆出来给你看",
            "clipboard": "✔ 翻完只放剪贴板，你自己粘",
        }[mode], "ok")

    # ---------------------------------------------------- 回话：发送按键

    def _load_key_choices(self, profile) -> None:
        """从现有的两串序列反推「开聊天框」和「发送」各是什么键，填进下拉。"""
        quick = profile.send_seq_quick or ""
        parts = [p.strip().lower() for p in quick.split(",") if p.strip()]
        chat = parts[0] if parts else ""
        send = parts[-1] if parts else ""
        self.chat_key_var.set(KEY_ALIAS.get(chat, chat))
        self.send_key_var.set(KEY_ALIAS.get(send, send))

    def _apply_key_choices(self) -> None:
        """按两个下拉重写序列 —— 用户只要知道"我的游戏按哪个键开聊天框"。"""
        chat = KEY_TO_ACTION.get(
            self.chat_key_var.get().strip(), self.chat_key_var.get().strip().lower()
        )
        send = KEY_TO_ACTION.get(
            self.send_key_var.get().strip(), self.send_key_var.get().strip().lower()
        )
        if not chat or not send:
            return
        self.seq_quick_var.set(f"{chat},text,{send}")
        self.seq_inline_var.set(f"backspace*100,text,{send}")
        self.seq_hint.configure(
            text="已按你选的键改好，点「保存发送按键」生效", foreground=MUTED
        )

    def _on_save_seqs(self) -> None:
        profile = self.app.cfg.profile()
        quick = self.seq_quick_var.get().strip()
        inline = self.seq_inline_var.get().strip()
        if not quick or not inline:
            self.seq_hint.configure(text="两串都不能留空", foreground=ERR_COLOR)
            return

        profile.send_seq_quick = quick
        profile.send_seq_inline = inline
        try:
            self.app.store.save(self.app.cfg)
        except OSError as exc:
            self.seq_hint.configure(text=f"保存失败：{exc}", foreground=ERR_COLOR)
            return
        self.seq_hint.configure(
            text="✔ 已保存，下次发送就用新的", foreground=OK_COLOR
        )

    def _reply_btn_text(self) -> str:
        lang = self.app.cfg.profile().reply_lang or DEFAULT_REPLY_LANG
        return f"翻成{lang}并复制"

    def _on_reply_lang(self) -> None:
        lang = (self.reply_lang_var.get() or "").strip()
        if not lang:
            return
        profile = self.app.cfg.profile()
        profile.reply_lang = lang
        try:
            self.app.store.save(self.app.cfg)
        except OSError as exc:
            self.set_reply_hint(f"保存失败：{exc}", "error")
            return
        self.reply_btn.configure(text=self._reply_btn_text())
        self.set_reply_hint(f"✔ 以后都翻成{lang}", "ok")

    def _on_reply(self) -> None:
        text = self.reply_input.get("1.0", "end-1c").strip()
        if not text:
            self.set_reply_hint("先打点中文", "error")
            return
        self.app.reply_to_foreign(text)

    def set_reply_hint(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.reply_hint.configure(text=text, foreground=color)

    def set_reply_busy(self, busy: bool) -> None:
        self.reply_btn.configure(
            text="翻译中…" if busy else self._reply_btn_text(),
            state="disabled" if busy else "normal",
        )

    def show_reply_result(self, text: str) -> None:
        self.reply_out.configure(text=f"→ {text}", foreground=OK_COLOR)

    def _build_glossary_section(self, parent) -> None:
        ttk.Label(parent, text="术语表", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(
            card,
            text="不同游戏里同一个词意思可能不一样，所以按游戏分开存。\n"
                 "同一时间只有一个分类生效 —— 就是左边选中的那个。\n"
                 "「默认」是空的，自己往里加；预置的「英雄联盟」那份可以\n"
                 "直接选中用，也可以复制一份改成别的游戏。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(card, style="Card.TFrame")
        body.pack(fill="both", expand=True)

        # ── 左：分类 ──
        left = ttk.Frame(body, style="Card.TFrame")
        left.pack(side="left", fill="y")

        ttk.Label(left, text="分类", style="CardMuted.TLabel").pack(anchor="w")

        self.glossary_list = tk.Listbox(
            left, height=13, width=11, exportselection=False,
            font=(FONT, 10), activestyle="none",
            bg=CARD, fg=TEXT, selectbackground=ACCENT,
            selectforeground="white", relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.glossary_list.pack(fill="y", pady=(2, 4))
        self.glossary_list.bind("<<ListboxSelect>>", self._on_switch_glossary)

        cat_btns = ttk.Frame(left, style="Card.TFrame")
        cat_btns.pack(fill="x")
        ttk.Button(cat_btns, text="＋", width=3,
                   command=self._on_add_glossary).pack(side="left")
        ttk.Button(cat_btns, text="改名", width=5,
                   command=self._on_rename_glossary).pack(side="left", padx=(3, 0))
        ttk.Button(cat_btns, text="删除", width=5,
                   command=self._on_delete_glossary).pack(side="left", padx=(3, 0))

        # ── 右：条目 ──
        # 必须 expand=True，否则表格拿不到剩余宽度，列会被挤掉
        right = ttk.Frame(body, style="Card.TFrame")
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self.glossary_caption = ttk.Label(right, text="", style="Card.TLabel")
        self.glossary_caption.pack(anchor="w", pady=(0, 2))

        table = ttk.Frame(right, style="Card.TFrame")
        table.pack(fill="both", expand=True)

        self.glossary_tree = ttk.Treeview(
            table, columns=("term", "translation", "mode"),
            show="headings", height=13,
        )
        self.glossary_tree.heading("term", text="词汇")
        self.glossary_tree.heading("translation", text="译文")
        self.glossary_tree.heading("mode", text="处理")
        self.glossary_tree.column("term", width=120, anchor="w", minwidth=90)
        self.glossary_tree.column("translation", width=200, anchor="w", minwidth=130)
        self.glossary_tree.column("mode", width=110, anchor="center", minwidth=90)

        scroll = ttk.Scrollbar(table, orient="vertical",
                               command=self.glossary_tree.yview)
        self.glossary_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.glossary_tree.pack(side="left", fill="both", expand=True)

        self.glossary_tree.tag_configure("keep", foreground=MUTED)
        self.glossary_tree.bind("<Double-1>", self._on_edit_entry)

        row = ttk.Frame(right, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="添加", width=6,
                   command=self._on_add_entry).pack(side="left")
        ttk.Button(row, text="编辑", width=6,
                   command=self._on_edit_entry).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="删除", width=6,
                   command=self._on_delete_entry).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="恢复默认", width=9,
                   command=self._on_reset_glossary).pack(side="left", padx=(4, 0))

        self.glossary_hint = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.glossary_hint.pack(anchor="w", pady=(6, 0))

        self.refresh_glossary()

    # ---------------------------------------------------------- 分类

    def _groups(self) -> list:
        return self.app.cfg.profile().glossaries or []

    def _active_name(self) -> str:
        return self.app.cfg.profile().active_glossary

    def _on_switch_glossary(self, _event=None) -> None:
        sel = self.glossary_list.curselection()
        if not sel:
            return
        name = self.glossary_list.get(sel[0])
        if name == self._active_name():
            return
        # 切之前先把表格里改过的内容写回内存，不然会丢
        self._sync_entries()
        self.app.cfg.profile().active_glossary = name
        self.refresh_glossary()
        self.app.save_glossaries(self._groups(), name)

    def _on_add_glossary(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("新建分类", "分类叫什么？", parent=self.root)
        name = (name or "").strip()
        if not name:
            return
        if any(g.get("name") == name for g in self._groups()):
            self.set_glossary_hint(f"已经有一个叫「{name}」的分类了", "error")
            return

        self._sync_entries()
        self._groups().append({"name": name, "entries": []})
        self.app.cfg.profile().active_glossary = name
        self.app.save_glossaries(self._groups(), name)

    def _on_rename_glossary(self) -> None:
        from tkinter import simpledialog

        old = self._active_name()
        new = simpledialog.askstring("分类改名", "新名字：", initialvalue=old,
                                     parent=self.root)
        new = (new or "").strip()
        if not new or new == old:
            return
        if any(g.get("name") == new for g in self._groups()):
            self.set_glossary_hint(f"已经有一个叫「{new}」的分类了", "error")
            return

        self._sync_entries()
        for group in self._groups():
            if group.get("name") == old:
                group["name"] = new
        self.app.save_glossaries(self._groups(), new)

    def _on_delete_glossary(self) -> None:
        name = self._active_name()
        groups = self._groups()
        if len(groups) <= 1:
            self.set_glossary_hint("至少得留一个分类", "error")
            return

        remaining = [g for g in groups if g.get("name") != name]
        self.app.save_glossaries(remaining, remaining[0]["name"])
        self.set_glossary_hint(f"已删掉「{name}」", "ok")

    # ---------------------------------------------------------- 术语表增删改

    def _entries(self) -> list:
        """当前表格里的条目 —— 表格就是唯一数据源，改完再整体保存。"""
        out = []
        for iid in self.glossary_tree.get_children():
            values = self.glossary_tree.item(iid, "values")
            out.append({
                "term": values[0],
                "translation": values[1],
                "translate": values[2] == "翻译",
            })
        return out

    def _on_add_entry(self) -> None:
        def done(entry):
            if entry is None:
                return
            self._insert_row(entry)
            self._persist()

        GlossaryEntryDialog(self.root, None, done)

    def _on_edit_entry(self, _event=None) -> None:
        sel = self.glossary_tree.selection()
        if not sel:
            self.set_glossary_hint("先选中一行再点编辑", "error")
            return
        iid = sel[0]
        values = self.glossary_tree.item(iid, "values")
        current = {
            "term": values[0],
            "translation": values[1],
            "translate": values[2] == "翻译",
        }

        def done(entry):
            if entry is None:
                return
            self._fill_row(iid, entry)
            self._persist()

        GlossaryEntryDialog(self.root, current, done)

    def _on_delete_entry(self) -> None:
        sel = self.glossary_tree.selection()
        if not sel:
            self.set_glossary_hint("先选中要删的行", "error")
            return
        for iid in sel:
            self.glossary_tree.delete(iid)
        self._persist()

    def _on_reset_glossary(self) -> None:
        """把当前分类恢复成预置的那批（只影响这一个分类）。"""
        from config import DEFAULT_GLOSSARY

        self.glossary_tree.delete(*self.glossary_tree.get_children())
        for entry in DEFAULT_GLOSSARY:
            self._insert_row(dict(entry))
        self._persist()

    def _fill_row(self, iid: str, entry: dict) -> None:
        mode = "翻译" if entry.get("translate", True) else "保持英文"
        self.glossary_tree.item(
            iid,
            values=(entry["term"], entry.get("translation", ""), mode),
            tags=() if entry.get("translate", True) else ("keep",),
        )

    def _insert_row(self, entry: dict) -> str:
        iid = self.glossary_tree.insert("", "end", values=("", "", ""))
        self._fill_row(iid, entry)
        return iid

    def refresh_glossary(self) -> None:
        """重画分类列表和条目表格。"""
        groups = self._groups()
        active = self._active_name()

        self.glossary_list.delete(0, "end")
        for group in groups:
            self.glossary_list.insert("end", group.get("name", ""))

        names = [g.get("name") for g in groups]
        if active in names:
            idx = names.index(active)
            self.glossary_list.selection_clear(0, "end")
            self.glossary_list.selection_set(idx)
            self.glossary_list.see(idx)

        # 保持英文的排前面，要翻译的排后面 —— 一眼就能看出哪些词不动
        entries = sorted(
            (e for e in self.app.active_entries()
             if isinstance(e, dict) and e.get("term")),
            key=lambda e: bool(e.get("translate", True)),
        )

        self.glossary_tree.delete(*self.glossary_tree.get_children())
        for entry in entries:
            self._insert_row(entry)

        kept = sum(1 for e in entries if not e.get("translate", True))
        self.glossary_caption.configure(
            text=f"「{active}」　{len(entries)} 条，其中 {kept} 条保持英文"
        )

    def _sync_entries(self) -> None:
        """把表格里的内容写回内存中当前那个分类。

        切分类、增删改之前都得先调它，否则改完的东西会丢。
        """
        active = self._active_name()
        for group in self._groups():
            if group.get("name") == active:
                group["entries"] = self._entries()
                return

    def _persist(self) -> None:
        """写回内存 + 落盘，增删改之后统一走这个。"""
        self._sync_entries()
        self.app.save_glossaries(self._groups(), self._active_name())

    def set_glossary_hint(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.glossary_hint.configure(text=text, foreground=color)

    def _build_history_page(self, parent) -> None:
        ttk.Label(parent, text="翻译记录", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(
            card,
            text="翻过的内容都记在这里 —— 对局中想回看之前谁说了什么，就翻这个。\n"
                 "最新的在最上面。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(0, 6))

        table = ttk.Frame(card, style="Card.TFrame")
        table.pack(fill="both", expand=True)

        self.history_tree = ttk.Treeview(
            table, columns=("time", "text"), show="headings", height=16,
        )
        self.history_tree.heading("time", text="时间")
        self.history_tree.heading("text", text="内容")
        self.history_tree.column("time", width=120, anchor="w")
        self.history_tree.column("text", width=460, anchor="w")

        scroll = ttk.Scrollbar(table, orient="vertical",
                               command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.history_tree.pack(side="left", fill="both", expand=True)

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="刷新", width=6,
                   command=self.refresh_history).pack(side="left")
        ttk.Button(row, text="打开原文件", width=10,
                   command=self.app.open_history).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="清空记录", width=9,
                   command=self._on_clear_history).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="复制选中", width=9,
                   command=self._on_copy_entry).pack(side="left", padx=(6, 0))

        self.history_hint = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.history_hint.pack(anchor="w", pady=(6, 0))

        self.refresh_history()

    def refresh_history(self) -> None:
        """从 history.jsonl 读最近的内容，最新的排最上面。"""
        from config import PROJECT_DIR

        path = PROJECT_DIR / "history.jsonl"
        self.history_tree.delete(*self.history_tree.get_children())

        if not path.exists():
            self.set_history_hint("还没有记录 —— 翻一次聊天就会有了")
            return

        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError as exc:
            self.set_history_hint(f"读不了记录文件：{exc}", "error")
            return

        shown = 0
        for line in reversed(lines[-400:]):  # 只显示最近 400 条
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = (record.get("text") or "").replace("\n", "　·　")
            # 提出来单独放一列，方便复制
            item = self.history_tree.insert(
                "", "end", values=(record.get("time", ""), text)
            )
            self.history_tree.set(item, "text", text)
            shown += 1

        self.set_history_hint(
            f"共 {len(lines)} 条记录，显示最近 {shown} 条"
        )

    def _on_clear_history(self) -> None:
        from config import PROJECT_DIR

        if not messagebox.askyesno(
            "清空记录", "确定要清空所有翻译记录吗？这个删了就找不回来了。",
            parent=self.root,
        ):
            return

        path = PROJECT_DIR / "history.jsonl"
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.set_history_hint(f"清空失败：{exc}", "error")
            return

        self.refresh_history()
        self.set_history_hint("记录已清空", "ok")

    def _on_copy_entry(self) -> None:
        sel = self.history_tree.selection()
        if not sel:
            self.set_history_hint("先选中一行", "error")
            return
        text = self.history_tree.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_history_hint("已复制到剪贴板", "ok")

    def set_history_hint(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.history_hint.configure(text=text, foreground=color)

    def _build_hotkey_section(self, parent) -> None:
        ttk.Label(parent, text="热键", style="Section.TLabel").pack(
            anchor="w", pady=(16, 0)
        )

        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(6, 0))

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")

        # 六个热键全部可改，行和按钮都由 HOTKEY_FIELDS 生成
        self._hotkey_labels = {}
        for i, (field, desc, _default) in enumerate(HOTKEY_FIELDS):
            lbl = ttk.Label(grid, text="", style="Card.TLabel",
                            font=(FONT_MONO, 10, "bold"), width=17, anchor="w")
            lbl.grid(row=i, column=0, sticky="w", pady=1)
            self._hotkey_labels[field] = lbl

            ttk.Label(grid, text=desc, style="CardMuted.TLabel").grid(
                row=i, column=1, sticky="w", padx=(14, 0), pady=1
            )

            ttk.Button(
                grid, text="修改", width=6,
                command=lambda f=field: self._change_hotkey(f),
            ).grid(row=i, column=2, sticky="e", padx=(8, 0))

        grid.columnconfigure(1, weight=1)

        # 工作方式：标准(RegisterHotKey) / 轮询(GetAsyncKeyState)
        mode_row = ttk.Frame(card, style="Card.TFrame")
        mode_row.pack(fill="x", pady=(12, 0))
        ttk.Label(mode_row, text="工作方式",
                  style="CardMuted.TLabel").pack(side="left")

        self.hotkey_mode_var = tk.StringVar(value=self.app.cfg.hotkey_mode)
        for label, value in (("标准", "register"),
                             ("轮询", "poll"),
                             ("键盘钩子", "hook")):
            ttk.Radiobutton(
                mode_row, text=label, value=value,
                variable=self.hotkey_mode_var, command=self._on_hotkey_mode,
            ).pack(side="left", padx=(0, 10))

        ttk.Label(
            card,
            text="默认是「轮询」—— 没有副作用，对游戏的兼容性也比「标准」好。\n"
                 "LOL 这类游戏会用底层键盘独占把「标准」整个屏蔽掉，那时候\n"
                 "热键按了没反应。真遇到再往下试「键盘钩子」（它挂得更靠前，\n"
                 "AutoHotkey 就是靠它进游戏的，但安全软件可能看它不顺眼）。",
            style="CardMuted.TLabel", justify="left",
        ).pack(anchor="w", pady=(4, 0))

        self.hotkey_note = ttk.Label(
            card, text="", style="CardMuted.TLabel", wraplength=400,
            justify="left",
        )
        self.hotkey_note.pack(anchor="w", pady=(6, 0))
        self.refresh_hotkeys()

    def _on_hotkey_mode(self) -> None:
        self.app.set_hotkey_mode(self.hotkey_mode_var.get())

    def refresh_hotkeys(self) -> None:
        profile = self.app.cfg.profile()
        for field, lbl in self._hotkey_labels.items():
            spec = getattr(profile, field, "") or HOTKEY_DEFAULTS.get(field, "")
            lbl.configure(text=display_hotkey(spec))

    def _change_hotkey(self, field: str) -> None:
        profile = self.app.cfg.profile()
        current = getattr(profile, field, "") or HOTKEY_DEFAULTS[field]
        HotkeyDialog(
            self.root,
            current,
            lambda spec: self.app.apply_hotkey(field, spec),
        )

    def set_hotkey_note(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.hotkey_note.configure(text=text, foreground=color)

    # ---------------------------------------------------------- 底部

    def _build_footer(self, parent) -> None:
        ttk.Separator(parent).pack(fill="x", pady=(16, 10))

        status_row = ttk.Frame(parent)
        status_row.pack(fill="x")

        self.status_dot = tk.Canvas(
            status_row, width=10, height=10, bg=BG, highlightthickness=0
        )
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.status_dot.pack(side="left", padx=(0, 8))

        self.status_var = tk.StringVar(value="正在初始化…")
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(12, 0))

        ttk.Button(
            buttons, text="最小化到后台", command=self.app.hide_window,
        ).pack(side="left")

        ttk.Button(
            buttons, text="翻译记录", command=self.app.open_history,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            buttons, text="退出程序", command=self.app.shutdown,
        ).pack(side="right")

        ttk.Label(
            parent, text=CREDIT, style="Muted.TLabel", anchor="center",
        ).pack(fill="x", pady=(14, 0))

    def set_status(self, text: str, kind: str = "info") -> None:
        fill = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        self.status_var.set(text)
        try:
            self.status_dot.itemconfigure(self._dot, fill=fill)
        except tk.TclError:
            pass

    # ---------------------------------------------------------- 窗口

    def show_page(self, key: str) -> None:
        """切换右侧显示哪一页，并把左边对应的按钮高亮。"""
        for name, frame in self._pages.items():
            if name == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        for name, btn in self._nav_buttons.items():
            active = name == key
            btn.configure(bg=ACCENT if active else CARD,
                          fg="white" if active else TEXT)

        self._current_page = key

    def center(self) -> None:
        """按内容算窗口大小，但绝不超出屏幕。"""
        self.root.update_idletasks()
        w = max(WINDOW_W, self.root.winfo_reqwidth())
        # 每页高度不同，按最高的那页留位置，切换时窗口不跳
        tallest = 0
        current = getattr(self, "_current_page", "api")
        for name, frame in self._pages.items():
            frame.pack(fill="both", expand=True)
            self.root.update_idletasks()
            tallest = max(tallest, self.root.winfo_reqheight())
            if name != current:
                frame.pack_forget()
        self.root.update_idletasks()

        h = min(tallest + 8, self.root.winfo_screenheight() - 80)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(20, (sh - h) // 2 - 40)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def focus_api_entry(self) -> None:
        self.api_entry.focus_set()
        self.api_entry.select_range(0, "end")


# ---------------------------------------------------------------- 首次设置对话框


class ApiKeyDialog(tk.Toplevel):
    """首次使用时的 API key 输入框。"""

    def __init__(self, master, on_submit) -> None:
        super().__init__(master)
        self.on_submit = on_submit

        self.title("首次使用")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        box = ttk.Frame(self, padding=(24, 20, 24, 18))
        box.pack(fill="both", expand=True)

        logo_path = ASSETS / "logo.png"
        if logo_path.exists():
            try:
                from PIL import Image, ImageTk

                im = Image.open(logo_path).convert("RGBA")
                target_w = 132
                if im.width > target_w:
                    ratio = target_w / im.width
                    im = im.resize(
                        (target_w, max(1, round(im.height * ratio))),
                        Image.LANCZOS,
                    )
                self._logo = ImageTk.PhotoImage(im)
                ttk.Label(box, image=self._logo, background=BG).pack()
            except Exception:
                pass

        ttk.Label(box, text="欢迎使用", style="Title.TLabel").pack(pady=(10, 2))
        ttk.Label(
            box,
            text="先填一个 DeepSeek API Key 就能开始。\n"
                 "去 platform.deepseek.com 申请，充值几块钱能用很久。\n"
                 "（想用别家 AI 也行，填完进「API Key」那一页能改。）",
            style="Muted.TLabel",
            justify="center",
        ).pack(pady=(0, 14))

        self.key_var = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.key_var, show="•", width=42)
        entry.pack(fill="x")
        entry.focus_set()

        ttk.Label(
            box, text="key 只存在你本机的 config.json 里",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 14))

        self.hint = ttk.Label(box, text="", style="Muted.TLabel", wraplength=360)
        self.hint.pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(box)
        row.pack(fill="x")

        self.ok_btn = ttk.Button(
            row, text="保存并开始", style="Accent.TButton", command=self._submit
        )
        self.ok_btn.pack(side="right")

        ttk.Button(row, text="稍后再说", command=self._skip).pack(
            side="right", padx=(0, 8)
        )

        entry.bind("<Return>", lambda _e: self._submit())
        self.bind("<Escape>", lambda _e: self._skip())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2 - 60}")

    def _submit(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self.hint.configure(text="key 不能为空", foreground=ERR_COLOR)
            return
        self.destroy()
        self.on_submit(key)

    def _skip(self) -> None:
        self.destroy()
        self.on_submit(None)

    def set_busy(self, text: str) -> None:
        self.hint.configure(text=text, foreground=MUTED)
        self.ok_btn.configure(state="disabled")


# ---------------------------------------------------------------- 热键设置对话框


class HotkeyDialog(tk.Toplevel):
    """让用户直接按下组合键来设置热键。"""

    MOD_SHIFT = 0x0001
    MOD_CTRL = 0x0004
    MOD_ALT = 0x0008 | 0x20000  # Windows 上 Alt 的位在不同 Tk 版本不一致，两个都认

    MODIFIER_KEYS = {
        "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
        "Super_L", "Super_R", "Win_L", "Win_R", "Caps_Lock", "Num_Lock",
        "ISO_Level3_Shift", "Mode_switch",
    }

    def __init__(self, master, current: str, on_submit) -> None:
        super().__init__(master)
        self.on_submit = on_submit
        self.result: str | None = None

        self.title("设置热键")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        box = ttk.Frame(self, padding=(26, 20, 26, 18))
        box.pack(fill="both", expand=True)

        ttk.Label(box, text="按下你想用的组合键", style="Title.TLabel").pack()
        ttk.Label(
            box,
            text="想加 Ctrl / Alt / Shift 就加，不想加也行\n"
                 "F1–F12 可以单按；字母数字单按会挡住打字",
            style="Muted.TLabel",
            justify="center",
        ).pack(pady=(6, 6))

        ttk.Label(
            box,
            text="⚠ 按之前先把输入法切到英文 —— 中文标点模式会吃掉\n"
                 "分号、引号这类符号键，让它们变成别的键",
            style="Muted.TLabel",
            foreground=WARN_COLOR,
            justify="center",
            wraplength=340,
        ).pack(pady=(0, 14))

        self.preview_var = tk.StringVar(value=display_hotkey(current))
        ttk.Label(
            box, textvariable=self.preview_var,
            font=(FONT_MONO, 16, "bold"), anchor="center",
        ).pack(fill="x", pady=(0, 4))

        # wraplength 必须设：提示文字会变长（比如那段"会挡住打字"的警告），
        # 不折行的话会被窗口宽度切掉，用户根本看不到后半句。
        self.hint = ttk.Label(
            box, text="等待按键…", style="Muted.TLabel",
            wraplength=340, justify="center",
        )
        self.hint.pack(pady=(0, 14), fill="x")

        row = ttk.Frame(box)
        row.pack(fill="x")
        self.ok_btn = ttk.Button(
            row, text="确定", style="Accent.TButton",
            command=self._submit, state="disabled",
        )
        self.ok_btn.pack(side="right")
        ttk.Button(row, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.bind("<KeyPress>", self._on_key)
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2 - 60
        # 记下来：后面提示文字变长要重算高度，那时 winfo_width 可能不可靠
        self._base_width, self._base_x, self._base_y = w, x, y
        self.geometry(f"{w}x{h}+{x}+{y}")

        # grab_set 在窗口还没映射好时会抛异常，不能让它把 __init__ 打断
        try:
            self.grab_set()
        except tk.TclError:
            pass
        # 无边框/新映射的窗口拿焦点有时要等一拍，隔一下再抢一次
        self.focus_force()
        self.after(80, self._grab_focus)

    def _grab_focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _set_hint(self, text: str, kind: str = "muted") -> None:
        """换提示文字，并把窗口高度重新适配一遍。

        窗口尺寸是 __init__ 时按初始的短文本算好的，之后文字变长如果不重算，
        多出来的行就会被切掉。
        """
        color = {"ok": OK_COLOR, "error": ERR_COLOR, "warn": WARN_COLOR}.get(
            kind, MUTED
        )
        self.hint.configure(text=text, foreground=color)

        self.update_idletasks()
        try:
            # 宽度用初始化时算好的：winfo_width 在窗口还没映射时会返回 1
            w = getattr(self, "_base_width", None) or self.winfo_reqwidth()
            h = self.winfo_reqheight()  # 这个反映折行后的真实高度，是准的
            x = self.winfo_x() or getattr(self, "_base_x", 0)
            y = self.winfo_y() or getattr(self, "_base_y", 0)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass

    def _on_key(self, event) -> str:
        if event.keysym in self.MODIFIER_KEYS:
            return "break"  # 只按了 Ctrl/Alt/Shift 本身，还没到主键

        spec = self._build_spec(event)
        if spec is None:
            # 把原始数据报出来 —— 认不出来的键，光看现象没法定位
            self._set_hint(
                f"这个键用不了（键码 {event.keycode}）。\n"
                f"换个键，或者先把输入法切成英文再按",
                "error",
            )
            return "break"

        self.result = spec
        self.preview_var.set(display_hotkey(spec))

        # 不带修饰键的字母/数字会把它从游戏和日常打字里"吃掉"。
        # 提醒一句但还是允许 —— 怎么用是用户自己的事。
        is_function_key = spec.startswith("f") and spec[1:].isdigit()
        if "<" in spec or is_function_key:
            self._set_hint("按「确定」保存", "ok")
        else:
            self._set_hint(
                "⚠ 单按这个键会挡住你正常打字，\n建议再加个 Ctrl 或 Alt",
                "warn",
            )

        self.ok_btn.configure(state="normal")
        return "break"

    def _build_spec(self, event) -> str | None:
        """把按键事件转成 '<ctrl>+<alt>+t' 或 'f9' 这种规格字符串。"""
        # 修饰键直接问系统，不用 event.state。
        # Tk 在 Windows 上把 Alt 报在 0x20000 位，很容易误判成"一直按着 Alt"，
        # 结果不管按什么键都会被强行拼上 <alt>，还去不掉。
        mods = winutil.get_pressed_modifiers()

        # 主键优先用 keycode —— Windows 上它就是虚拟键码，RegisterHotKey
        # 要的正是它。keysym 是"键的名字"，会随键盘布局、输入法状态变化，
        # 认不出来时直接报 "??"，同一个键还能有好几种写法。
        name = vk_to_spec(int(event.keycode))

        if name is None:
            # 兜底：退回 keysym
            keysym = event.keysym
            if len(keysym) == 1:
                name = keysym.lower()
            else:
                name = KEYSYM_TO_SPEC.get(keysym)

        if name is None:
            return None

        if mods:
            return "<" + ">+<".join(mods) + ">+" + name
        return name  # 无修饰键：RegisterHotKey 本来就支持

    def _submit(self) -> None:
        spec = self.result
        self.destroy()
        if spec:
            self.on_submit(spec)


# ---------------------------------------------------------------- 术语编辑对话框


class GlossaryEntryDialog(tk.Toplevel):
    """编辑一条术语。双击表格里的行，或者点「添加」时弹出来。"""

    def __init__(self, master, current: dict | None, on_submit) -> None:
        super().__init__(master)
        self.on_submit = on_submit
        self.result = None
        current = current or {}

        self.title("编辑术语" if current else "添加术语")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        box = ttk.Frame(self, padding=(24, 20, 24, 18))
        box.pack(fill="both", expand=True)

        ttk.Label(box, text="词汇（游戏里出现的样子）",
                  style="Muted.TLabel").pack(anchor="w")
        self.term_var = tk.StringVar(value=current.get("term", ""))
        term_entry = ttk.Entry(box, textvariable=self.term_var, width=34)
        term_entry.pack(fill="x", pady=(2, 12))

        ttk.Label(box, text="译文", style="Muted.TLabel").pack(anchor="w")
        self.trans_var = tk.StringVar(value=current.get("translation", ""))
        ttk.Entry(box, textvariable=self.trans_var,
                  width=34).pack(fill="x", pady=(2, 12))

        ttk.Label(box, text="怎么处理", style="Muted.TLabel").pack(anchor="w")
        self.mode_var = tk.StringVar(
            value="translate" if current.get("translate", True) else "keep"
        )
        ttk.Radiobutton(box, text="翻译成上面的译文", value="translate",
                        variable=self.mode_var).pack(anchor="w", pady=(2, 0))
        ttk.Radiobutton(box, text="保持英文原样不动", value="keep",
                        variable=self.mode_var).pack(anchor="w")

        self.hint = ttk.Label(box, text="", style="Muted.TLabel",
                              wraplength=320)
        self.hint.pack(anchor="w", pady=(10, 8))

        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="确定", style="Accent.TButton",
                   command=self._submit).pack(side="right")
        ttk.Button(row, text="取消",
                   command=self.destroy).pack(side="right", padx=(0, 8))

        term_entry.focus_set()
        self.bind("<Return>", lambda _e: self._submit())
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2 - 80}")
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _submit(self) -> None:
        term = self.term_var.get().strip()
        if not term:
            self.hint.configure(text="词汇不能为空", foreground=ERR_COLOR)
            return

        translate = self.mode_var.get() == "translate"
        translation = self.trans_var.get().strip()
        if translate and not translation:
            self.hint.configure(text="选了「翻译」就得填译文",
                                foreground=ERR_COLOR)
            return

        self.result = {
            "term": term,
            "translation": translation or term,
            "translate": translate,
        }
        callback = self.on_submit
        self.destroy()
        callback(self.result)
