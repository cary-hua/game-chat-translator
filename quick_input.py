"""按热键弹出来的小输入框：打中文，回车，翻好的外文自动发进游戏。

和悬浮窗（overlay.py）的关键区别：悬浮窗挂 WS_EX_NOACTIVATE，从不抢焦点，
只负责显示；这个**必须能接收键盘** —— 你就是要往里面打字。所以它会短暂地
把游戏的焦点拿走，发出消息后再还给游戏。

只在窗口化 / 无边框窗口化下可用。独占全屏时它会像悬浮窗一样被压在游戏底下。
"""

import tkinter as tk

import winutil
from gui import ACCENT, BORDER, CARD, ERR_COLOR, FONT, MUTED, OK_COLOR, TEXT

WIDTH = 480
HEIGHT = 118
BOTTOM_MARGIN = 170  # 离屏幕下边缘多远：避开大部分游戏的聊天框位置

HINT = "回车发送　·　Shift+回车换行　·　Esc 取消"
CONFIRM_HINT = "回车发出　·　Esc 算了（不会发出去）"


class QuickInput:
    """一次只开一个。open() 显示并抢焦点，close() 关掉。"""

    def __init__(self, master: tk.Misc, on_submit, on_confirm=None) -> None:
        self.master = master
        self.on_submit = on_submit    # 回车了，把这段中文拿去翻（main.py 干）
        self.on_confirm = on_confirm  # 译文摆出来后，又按了一次回车 —— 真发

        self._top: tk.Toplevel | None = None
        self._entry: tk.Text | None = None
        self._status: tk.Label | None = None
        self._busy = False
        # "input" = 在打字，"confirm" = 译文摆出来了、等你点头
        self._mode = "input"
        # 每次开框自增。翻译是异步的，回来时对一下号 ——
        # 你在翻译途中按了 Esc，那次的结果就不该再发出去了。
        self._token = 0

    # ---------------------------------------------------------- 对外

    def is_open(self) -> bool:
        return self._top is not None

    def token(self) -> int:
        return self._token

    def open(self) -> None:
        """弹出并抢焦点。已经开着就提到最前面，不重复建。"""
        if self._top is not None:
            self._grab_focus()
            return

        top = tk.Toplevel(self.master)
        top.withdraw()  # 先藏起来：overrideredirect 必须在 withdraw 状态下设才生效
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=ACCENT)  # 露出的 1px 当描边

        inner = tk.Frame(top, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._entry = tk.Text(
            inner, height=2, wrap="word", font=(FONT, 12),
            relief="flat", highlightthickness=0, bg=CARD, fg=TEXT,
            insertbackground=TEXT, padx=10, pady=8,
        )
        self._entry.pack(fill="both", expand=True)

        self._status = tk.Label(
            inner, text=HINT, anchor="w", bg=CARD, fg=MUTED,
            font=(FONT, 9), padx=10,
        )
        self._status.pack(fill="x", pady=(0, 6))

        self._top = top
        self._busy = False
        self._mode = "input"
        self._token += 1

        self._entry.bind("<Return>", self._on_return)
        self._entry.bind("<Shift-Return>", lambda _e: None)  # 换行，放行
        top.bind("<Escape>", lambda _e: self.close())

        top.deiconify()
        top.update_idletasks()  # ⚠️ 先让 Tk 知道尺寸，再走 SetWindowPos 定位
        self._place()
        self._grab_focus()

    def close(self) -> None:
        top, self._top = self._top, None
        self._entry = None
        self._status = None
        self._busy = False
        self._mode = "input"  # 下次开框重新从打字开始
        self._token += 1  # 在途的翻译作废 —— 关了框就别再发出去了
        if top is None:
            return
        try:
            top.destroy()
        except tk.TclError:
            pass

    def set_status(self, text: str, kind: str = "muted") -> None:
        color = {"ok": OK_COLOR, "error": ERR_COLOR}.get(kind, MUTED)
        try:
            if self._status is not None:
                self._status.configure(text=text, fg=color)
        except tk.TclError:
            pass

    def set_busy(self, busy: bool) -> None:
        """处理中就别让再按回车了 —— 否则连按两下会发两条。"""
        self._busy = busy
        if busy:
            self.set_status("处理中…", "muted")
        elif self._mode == "confirm":
            self.set_status(CONFIRM_HINT, "ok")
        else:
            self.set_status(HINT, "muted")

    def show_confirm(self, text: str) -> None:
        """翻完了先别发 —— 把译文摆回框里，等你点头。

        摆出来是有用的：模型偶尔会翻错、或者啰嗦一串，而你打的中文
        自己看得懂，一眼就能判断对不对。
        """
        self._mode = "confirm"
        self._busy = False
        if self._entry is not None:
            self._entry.delete("1.0", "end")
            self._entry.insert("1.0", text)
            self._entry.focus_set()
        self.set_status(CONFIRM_HINT, "ok")

    def focus_back(self) -> None:
        """翻完了但没发出去（比如自动发送关了），把焦点还给小框让你接着改。"""
        self._grab_focus()

    # ---------------------------------------------------------- 内部

    def _place(self) -> None:
        if self._top is None:
            return
        vleft, vtop, vw, vh = winutil.get_virtual_rect()
        x = vleft + (vw - WIDTH) // 2
        y = vtop + vh - HEIGHT - BOTTOM_MARGIN
        hwnd = winutil.get_hwnd(self._top)
        winutil.move_window(hwnd, x, y, WIDTH, HEIGHT)
        winutil.add_ex_style(hwnd, winutil.WS_EX_TOOLWINDOW)  # 别进 Alt+Tab
        winutil.set_topmost(hwnd, True)

    def _grab_focus(self) -> None:
        """从游戏手里把键盘焦点拿过来。

        游戏开着的时候本程序是后台进程，直接 SetForegroundWindow 会被前台锁定
        挡掉（winutil.force_foreground 里有绕法）。
        """
        if self._top is None:
            return
        hwnd = winutil.get_hwnd(self._top)
        winutil.restore_window(hwnd)
        winutil.force_foreground(hwnd)
        try:
            self._top.focus_force()
            if self._entry is not None:
                self._entry.focus_set()
        except tk.TclError:
            pass

    def _on_return(self, _event=None):
        if self._busy or self._entry is None:
            return "break"
        text = self._entry.get("1.0", "end-1c").strip()
        if not text:
            self.close()
            return "break"

        if self._mode == "confirm":
            # 摆出来的是译文，这一下回车才是"发"
            self.set_busy(True)
            if self.on_confirm is not None:
                self.on_confirm(text)
            else:
                self.close()
            return "break"

        self.set_busy(True)
        self.on_submit(text)
        return "break"
