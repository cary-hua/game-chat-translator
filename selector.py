"""全屏框选区域。

**必须是回调式**：全程序只有一个 Tk mainloop。
在别的线程里 root.mainloop() 会立刻报 "main thread is not in main loop"。
所以这里是 open(on_done)，而不是阻塞式 select() -> Rect。
"""

import tkinter as tk

import winutil
from config import Rect

HINT_TEXT = "拖动鼠标框选聊天栏区域　·　Enter 确认　·　Esc / 右键取消"


class RegionSelector:
    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._on_done = None

        self._vleft = 0
        self._vtop = 0

        self._origin: tuple[int, int] | None = None
        self._current: tuple[int, int, int, int] | None = None
        self._rect_id = None
        self._size_id = None
        self._hint_id = None

    # ---------------------------------------------------------- 对外

    def is_open(self) -> bool:
        return self._top is not None

    def open(self, on_done) -> None:
        """进入框选。on_done(Rect) 或 on_done(None) 表示取消。"""
        if self.is_open():
            return
        self._on_done = on_done

        vleft, vtop, vw, vh = winutil.get_virtual_rect()
        self._vleft, self._vtop = vleft, vtop

        top = tk.Toplevel(self.master)
        top.withdraw()  # 先藏起来
        # ⚠️ overrideredirect 必须在窗口还是 withdraw 状态时设置，否则无边框不生效
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.4)
        top.configure(bg="black")

        # ⚠️ 尺寸必须用 Tk 自己的 geometry 来设。
        # 光靠 SetWindowPos 改大小 Tk 是不知情的，deiconify() 时它会退回默认
        # 尺寸（实测变成 566x396），结果只有屏幕左上角一小块能被框选。
        top.geometry(f"{vw}x{vh}+0+0")

        canvas = tk.Canvas(top, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas
        self._top = top

        canvas.bind("<Button-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<Button-3>", lambda _e: self._finish(None))
        top.bind("<Escape>", lambda _e: self._finish(None))
        top.bind("<Return>", lambda _e: self._confirm())

        self._hint_id = canvas.create_text(
            vw // 2,
            48,
            text=HINT_TEXT,
            fill="#ffffff",
            font=("Microsoft YaHei UI", 16),
        )

        top.deiconify()
        top.update_idletasks()

        # 窗口映射之后再用 Win32 修正位置：Tk 的 geometry("+X+Y") 里 '-' 表示
        # 距右/下边缘的距离，表达不了左侧副屏那种绝对负坐标。
        hwnd = winutil.get_hwnd(top)
        winutil.move_window(hwnd, vleft, vtop, vw, vh)

        # overrideredirect 窗口不会自动拿到键盘焦点，Esc/Enter 会失灵
        top.lift()
        top.focus_force()
        try:
            top.grab_set()
        except tk.TclError:
            pass

    # ---------------------------------------------------------- 坐标

    def _to_canvas(self, x_root: int, y_root: int) -> tuple[int, int]:
        """屏幕绝对坐标 -> 画布坐标。"""
        return x_root - self._vleft, y_root - self._vtop

    # ---------------------------------------------------------- 鼠标

    def _on_press(self, event) -> None:
        self._origin = (event.x_root, event.y_root)
        self._current = None
        for attr in ("_rect_id", "_size_id"):
            item = getattr(self, attr)
            if item is not None:
                self._canvas.delete(item)
                setattr(self, attr, None)
        if self._hint_id is not None:
            self._canvas.itemconfigure(self._hint_id, text=HINT_TEXT)

    def _on_drag(self, event) -> None:
        if self._origin is None:
            return

        x0, y0 = self._origin
        x1, y1 = event.x_root, event.y_root
        cx0, cy0 = self._to_canvas(x0, y0)
        cx1, cy1 = self._to_canvas(x1, y1)

        if self._rect_id is None:
            self._rect_id = self._canvas.create_rectangle(
                cx0, cy0, cx1, cy1, outline="#ff3b30", width=2
            )
        else:
            self._canvas.coords(self._rect_id, cx0, cy0, cx1, cy1)

        label = f"{abs(x1 - x0)} x {abs(y1 - y0)}"
        lx, ly = min(cx0, cx1), min(cy0, cy1)
        # 贴近屏幕顶边时把标签翻到矩形内侧，否则会被切掉
        ly = ly + 22 if ly < 40 else ly - 20

        if self._size_id is None:
            self._size_id = self._canvas.create_text(
                lx, ly, text=label, anchor="w",
                fill="#ffffff", font=("Microsoft YaHei UI", 12),
            )
        else:
            self._canvas.coords(self._size_id, lx, ly)
            self._canvas.itemconfigure(self._size_id, text=label)

    def _on_release(self, event) -> None:
        if self._origin is None:
            return

        x0, y0 = self._origin
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)

        if w < 20 or h < 10:
            self._current = None
            if self._hint_id is not None:
                self._canvas.itemconfigure(
                    self._hint_id, text="区域太小了　·　重新拖一次"
                )
            return

        self._current = (left, top, w, h)
        if self._hint_id is not None:
            self._canvas.itemconfigure(
                self._hint_id,
                text=f"已选 {w} x {h}　·　Enter 确认　·　Esc / 右键重来",
            )

    # ---------------------------------------------------------- 收尾

    def _confirm(self) -> None:
        if self._current is None:
            return
        left, top, w, h = self._current
        self._finish(Rect(left=left, top=top, width=w, height=h))

    def _finish(self, rect: Rect | None) -> None:
        callback = self._on_done
        self._on_done = None
        self._destroy()
        if callback is not None:
            callback(rect)

    def _destroy(self) -> None:
        top = self._top
        self._top = None
        self._canvas = None
        self._rect_id = None
        self._size_id = None
        self._hint_id = None
        self._origin = None

        if top is None:
            return
        try:
            top.grab_release()
        except tk.TclError:
            pass
        try:
            top.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    # 手动验证：python selector.py
    import sys

    winutil.set_dpi_awareness()  # 必须在 Tk 之前

    root = tk.Tk()
    root.withdraw()
    print("虚拟桌面:", winutil.get_virtual_rect())

    selector = RegionSelector(root)

    def on_done(rect):
        if rect is None:
            print("已取消")
        else:
            print(f"选中：left={rect.left} top={rect.top} "
                  f"width={rect.width} height={rect.height}")
            # 立刻用它截一张图，验证坐标和内容吻合
            try:
                from capture import ScreenCapture
                from config import CaptureConfig

                frame = ScreenCapture(CaptureConfig()).capture(rect)
                with open("debug_selection.jpg", "wb") as f:
                    f.write(frame.data)
                print(f"已截图验证 -> debug_selection.jpg "
                      f"({frame.width}x{frame.height}, {frame.size_kb():.1f} KB)")
            except Exception as exc:
                print("截图验证失败:", exc)
        root.quit()

    root.after(200, lambda: selector.open(on_done))
    root.mainloop()
    sys.exit(0)
