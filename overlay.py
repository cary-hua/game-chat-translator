"""悬浮窗：显示翻译结果。

核心是"不抢游戏焦点"。靠三个窗口扩展样式：
  WS_EX_NOACTIVATE  —— 点击它不会激活窗口，游戏的键盘焦点不受影响
  WS_EX_TOOLWINDOW  —— 不出现在 Alt+Tab 列表里
  WDA_EXCLUDEFROMCAPTURE —— 从屏幕捕获中消失，防止自己被截进去形成翻译回环
"""

import tkinter as tk
import tkinter.font

import winutil
from config import OverlayConfig, Rect


class OverlayWindow:
    def __init__(self, master: tk.Misc, cfg: OverlayConfig) -> None:
        self.master = master
        self.cfg = cfg

        self._top: tk.Toplevel | None = None
        self._frame: tk.Frame | None = None
        self._text: tk.Text | None = None
        self._hwnd: int | None = None

        self._visible = False
        self._anchor: Rect | None = None
        self._excluded = False

        self._drag_offset: tuple[int, int] | None = None
        self._topmost_job = None
        self._hide_job = None

        # 用户拖动结束后的回调，参数是 [x, y]，用来把位置存盘
        self.on_moved = None

        self._build()

    # ---------------------------------------------------------- 构建

    def _build(self) -> None:
        cfg = self.cfg

        top = tk.Toplevel(self.master)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", cfg.alpha)
        top.configure(bg="#101014")

        frame = tk.Frame(top, bg="#101014", padx=10, pady=8)
        frame.pack(fill="both", expand=True)

        text = tk.Text(
            frame,
            wrap="word",
            bg="#101014",
            fg="#e8e8ec",
            insertwidth=0,
            relief="flat",
            highlightthickness=0,
            borderwidth=0,
            font=(cfg.font_family, cfg.font_size),
            width=1,
            height=1,
            spacing1=2,
            spacing3=2,
            cursor="arrow",
        )
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

        for widget in (text, frame):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)

        self._top = top
        self._frame = frame
        self._text = text

        # 窗口必须先真实创建过，扩展样式才挂得上
        top.deiconify()
        top.update_idletasks()
        hwnd = winutil.get_hwnd(top)
        self._hwnd = hwnd

        winutil.add_ex_style(hwnd, winutil.WS_EX_NOACTIVATE | winutil.WS_EX_TOOLWINDOW)
        self._excluded = winutil.exclude_from_capture(hwnd, True)

        top.withdraw()

    # ---------------------------------------------------------- 显示控制

    def is_visible(self) -> bool:
        return self._visible

    def set_anchor(self, rect: Rect | None) -> None:
        """告诉悬浮窗该贴着哪块区域显示（就是框选的聊天栏）。

        只赋值不碰 Tk 控件，所以 worker 线程直接调也安全。
        """
        self._anchor = rect

    def get_text(self) -> str:
        """当前窗口里实际显示的文本，用来跟模型返回的原文做核对。"""
        if self._text is None:
            return ""
        try:
            return self._text.get("1.0", "end-1c")
        except tk.TclError:
            return ""

    def window_rect(self) -> tuple[int, int, int, int] | None:
        """当前窗口的 (left, top, width, height)；没显示就返回 None。"""
        if not self._visible or self._top is None:
            return None
        try:
            return (
                self._top.winfo_x(),
                self._top.winfo_y(),
                self._top.winfo_width(),
                self._top.winfo_height(),
            )
        except tk.TclError:
            return None

    def show(self, anchor_rect: Rect | None = None) -> None:
        if anchor_rect is not None:
            self._anchor = anchor_rect
        if not self._visible:
            self._top.deiconify()
            self._visible = True
        # 排布必须放在窗口已映射之后：窗口还 withdraw 时 Tk 会用默认尺寸，
        # 把 SetWindowPos 设的结果盖掉。
        self._layout()
        # 注意：这里绝不用 lift() / focus_force()，它们会激活窗口抢走游戏焦点
        winutil.set_topmost(self._hwnd, True)
        # WDA 在窗口重新映射后不保证还在，每次显示都重申一次
        winutil.exclude_from_capture(self._hwnd, True)
        self._schedule_topmost()
        self._schedule_hide()

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._cancel_jobs()
        try:
            self._top.withdraw()
        except tk.TclError:
            pass

    def toggle(self, anchor_rect: Rect | None = None) -> None:
        if self._visible:
            self.hide()
        else:
            self.show(anchor_rect)

    # ---------------------------------------------------------- 内容

    def _set_text(self, content: str) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content)
        self._text.configure(state="disabled")
        # 滚到底部：最新的消息永远在最下面，内容超出高度时默认就该看到它。
        # 不调这句的话光标停在开头，用户看到的是最旧的那几行。
        self._text.see("end")

    def set_status(self, text: str) -> None:
        self._set_text(text)
        self.show()

    def begin_stream(self) -> None:
        # 流式正文不往窗口里贴 —— 里面大部分行等一下会被"只显示新增"过滤掉，
        # 先贴一遍再替换掉，用户看到的就是旧消息无意义地滚一轮。
        self._set_text("翻译中…")
        self.show()

    def append_delta(self, chunk: str) -> None:
        self._text.configure(state="normal")
        self._text.insert("end", chunk)
        self._text.configure(state="disabled")
        self._text.see("end")
        self._resize_later()
        self._schedule_hide()

    def end_stream(self) -> None:
        self._layout()

    def set_result(self, text: str, meta: str = "") -> None:
        content = text if not meta else f"{text}\n\n{meta}"
        self._set_text(content)
        self._layout()
        self.show()

    def set_error(self, message: str) -> None:
        self._set_text(f"⚠ {message}")
        self._layout()
        self.show()
        # 出错时给两倍时间，让用户来得及看
        self._schedule_hide(multiplier=2)

    # ---------------------------------------------------------- 布局

    def _content_size(self) -> tuple[int, int]:
        """按译文长短算合适的宽和高。

        短句给窄框、长句给宽框，不用用户自己去调。
        折行数是用字体度量估出来的，不依赖窗口先渲染 —— 那样会闪。
        """
        cfg = self.cfg
        try:
            text = self._text.get("1.0", "end-1c")
        except tk.TclError:
            return cfg.min_width, 56

        if not text.strip():
            return cfg.min_width, 56

        font = tkinter.font.Font(font=self._text.cget("font"))
        pad = 34  # 左右内边距
        line_h = font.metrics("linespace") + 4

        rows = text.split("\n")
        widest = max(font.measure(row) for row in rows)
        width = int(min(cfg.max_width, max(cfg.min_width, widest + pad)))

        # 按最终宽度估算折行后的总行数（向上取整）
        usable = max(40, width - pad)
        wrapped = sum(max(1, -(-font.measure(row) // usable)) for row in rows)

        # ⚠️ 高度必须落在行高的整数倍上，否则最后一行会被切掉半截 ——
        # 表现就是"最下面那条消息只露一半"。
        want = wrapped * line_h + 28
        avail = min(cfg.max_height, want)
        lines_fit = max(2, (avail - 28) // line_h)
        return width, int(lines_fit * line_h + 28)

    def _layout(self) -> None:
        cfg = self.cfg
        vleft, vtop, vw, vh = winutil.get_virtual_rect()
        w, h = self._content_size()

        # 用户手动拖过这个窗口，就固定在他放的位置。
        # 不加这一条的话，每次翻译调 _layout 都会把窗口弹回默认位置，
        # 拖了等于白拖。
        # 想恢复自动定位：把 config.json 里 overlay.last_pos 改成 null。
        if cfg.last_pos:
            x, y = int(cfg.last_pos[0]), int(cfg.last_pos[1])

        elif cfg.mode == "cover" and self._anchor is not None:
            x, y = self._anchor.left, self._anchor.top
            w = max(w, self._anchor.width)

        elif cfg.mode == "corner":
            gap = 16
            corner = cfg.corner or "bottom-right"
            x = vleft + vw - w - gap if "right" in corner else vleft + gap
            y = vtop + vh - h - gap if "bottom" in corner else vtop + gap

        else:  # side：默认贴框选区域右侧
            gap = cfg.anchor_gap
            if self._anchor is not None:
                x = self._anchor.left + self._anchor.width + gap
                y = self._anchor.top
                if x + w > vleft + vw:  # 右边放不下就翻到左边
                    x = self._anchor.left - w - gap
            else:
                x, y = vleft + vw - w - 16, vtop + 48

        # clamp 回屏内
        x = max(vleft, min(x, vleft + vw - w))
        y = max(vtop, min(y, vtop + vh - h))

        # ⚠️ 先让 Tk 知道尺寸。只用 SetWindowPos 改大小的话 Tk 不知情，
        # 窗口映射时会退回它自己的默认尺寸，把这里设的结果盖掉。
        self._top.geometry(f"{w}x{h}")
        self._top.update_idletasks()
        winutil.move_window(self._hwnd, x, y, w, h)

    def _resize_later(self) -> None:
        """流式追加时高频调用，节流一下，别每来一块就重排。"""
        if getattr(self, "_resize_job", None) is not None:
            return
        self._resize_job = self._top.after(120, self._do_resize)

    def _do_resize(self) -> None:
        self._resize_job = None
        if self._visible:
            self._layout()

    def apply_config(self, cfg: OverlayConfig) -> None:
        self.cfg = cfg
        self._text.configure(font=(cfg.font_family, cfg.font_size))
        self._top.attributes("-alpha", cfg.alpha)
        if self._visible:
            self._layout()

    # ---------------------------------------------------------- 拖动

    def _on_press(self, event) -> None:
        self._drag_offset = (
            self._top.winfo_x() - event.x_root,
            self._top.winfo_y() - event.y_root,
        )

    def _on_drag(self, event) -> None:
        if self._drag_offset is None:
            return
        dx, dy = self._drag_offset
        winutil.move_window(self._hwnd, event.x_root + dx, event.y_root + dy)

    def _on_release(self, _event) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        # 位置只在松手时记一次，不是每帧
        self.cfg.last_pos = [self._top.winfo_x(), self._top.winfo_y()]
        if callable(self.on_moved):
            self.on_moved(self.cfg.last_pos)

    # ---------------------------------------------------------- 定时器

    def _schedule_topmost(self) -> None:
        """游戏会抢走 topmost，定期重申一次。开销可以忽略。"""
        self._cancel_topmost()
        if self._visible:
            self._topmost_job = self._top.after(2000, self._tick_topmost)

    def _tick_topmost(self) -> None:
        self._topmost_job = None
        if not self._visible:
            return
        winutil.set_topmost(self._hwnd, True)
        self._schedule_topmost()

    def _schedule_hide(self, multiplier: float = 1.0) -> None:
        self._cancel_hide()
        seconds = self.cfg.auto_hide_sec
        if seconds and seconds > 0 and self._visible:
            self._hide_job = self._top.after(int(seconds * multiplier * 1000), self.hide)

    def _cancel_topmost(self) -> None:
        if self._topmost_job is not None:
            try:
                self._top.after_cancel(self._topmost_job)
            except tk.TclError:
                pass
            self._topmost_job = None

    def _cancel_hide(self) -> None:
        if self._hide_job is not None:
            try:
                self._top.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None

    def _cancel_jobs(self) -> None:
        self._cancel_topmost()
        self._cancel_hide()
        job = getattr(self, "_resize_job", None)
        if job is not None:
            try:
                self._top.after_cancel(job)
            except tk.TclError:
                pass
            self._resize_job = None

    # ---------------------------------------------------------- 销毁

    def destroy(self) -> None:
        self._cancel_jobs()
        if self._top is not None:
            try:
                self._top.destroy()
            except tk.TclError:
                pass
        self._top = None
        self._text = None
        self._visible = False


if __name__ == "__main__":
    # 手动验证：python overlay.py
    # 验证要点：窗口置顶、半透明、能拖动，且**不抢焦点**
    winutil.set_dpi_awareness()

    import time

    root = tk.Tk()
    root.withdraw()

    window = OverlayWindow(root, OverlayConfig())

    sample = (
        "正在验证悬浮窗……\n\n"
        "[Tom] 你好，打得好\n"
        "[Ann] 谢谢你，下次再来\n"
        "[Tom] 好的，祝好运\n\n"
        "—— 试着在记事本里打字，字符必须正常进去。\n"
        "—— Alt+Tab 列表里不应该有这个窗口。"
    )

    root.after(300, lambda: window.set_result(sample))

    def _quit():
        window.destroy()
        root.quit()

    root.after(30000, _quit)
    print("悬浮窗已显示，30 秒后自动关闭。")
    print("请打开记事本打字，确认按键没有被悬浮窗抢走。")
    root.mainloop()
