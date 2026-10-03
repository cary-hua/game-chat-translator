"""模拟一个游戏聊天栏，用来实验「只翻译新增的行」。

用法：
  1. 双击「模拟聊天栏.bat」打开它
  2. 它会每隔几秒冒一条新消息，你也可以自己按按钮加
  3. 用翻译器框选这个窗口（不要框到下面的按钮）
  4. 反复按热键：
       - 第一次会显示全部
       - 之后每次只显示新来的那几行
       - 没有新消息时提示「（没有新消息）」
"""

import tkinter as tk

BG = "#0d1117"
FG = "#e6edf3"
MUTED = "#8b949e"
CARD = "#161b22"
ACCENT = "#2f81f7"
FONT = ("Consolas", 11)

INTERVAL_MS = 4000  # 每隔多久自动冒一条

# 一段编排好的对话，从短到长，方便观察效果
MESSAGES = [
    ("Tom", "gg wp, nice game"),
    ("Ann", "thanks! wanna play again?"),
    ("Tom", "sure, one more round"),
    ("Ann", "brb, need water"),
    ("Tom", "ok take your time"),
    ("Ann", "back, ready when you are"),
    ("Tom", "rush b, dont stop"),
    ("Ann", "lol that was way too close"),
    ("Tom", "ez win, they had no chance"),
    ("Ann", "sry, i threw that one"),
    ("Tom", "np, happens to everyone"),
    ("Ann", "one more? i need to warm up"),
    ("Tom", "sure, last game for me"),
    ("Ann", "gg, you carried hard"),
]


class ChatSim:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.index = 0
        self.running = True
        self._job = None

        root.title("模拟聊天栏")
        root.configure(bg=BG)
        root.resizable(False, False)
        # 置顶：不然很容易被别的窗口盖住，框选的时候截到的是压在上面的东西
        root.attributes("-topmost", True)

        # 聊天区
        body = tk.Frame(root, bg=BG, padx=14, pady=10)
        body.pack(fill="both", expand=True)

        self.text = tk.Text(
            body, width=54, height=11,
            bg=BG, fg=FG, font=FONT,
            relief="flat", highlightthickness=1,
            highlightbackground="#30363d",
            wrap="word", padx=8, pady=6,
        )
        self.text.configure(state="disabled")
        self.text.pack(fill="both", expand=True)

        # 提示
        tk.Label(
            root,
            text="↑ 上面这块就是「聊天栏」，翻译的时候框选它",
            bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9),
        ).pack(pady=(0, 6))

        # 按钮
        bar = tk.Frame(root, bg=BG, padx=14)
        bar.pack(fill="x", pady=(0, 12))

        self._button(bar, "立刻来一条", self.add_one, primary=True).pack(side="left")
        self.toggle_btn = self._button(bar, "暂停", self.toggle)
        self.toggle_btn.pack(side="left", padx=(8, 0))
        self._button(bar, "清空", self.clear).pack(side="left", padx=(8, 0))

        self.status = tk.Label(
            bar, text="", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(side="right")

        self.center()
        self.schedule()

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent, text=text, command=command,
            bg=ACCENT if primary else CARD,
            fg="white", activebackground="#1f6feb" if primary else "#21262d",
            activeforeground="white",
            relief="flat", padx=12, pady=4, cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )

    def center(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2 - 80}")

    # ------------------------------------------------------ 加消息

    def add_one(self) -> None:
        if self.index >= len(MESSAGES):
            self.status.configure(text="对话演完了，点「清空」重来")
            return

        who, what = MESSAGES[self.index]
        self.index += 1

        self.text.configure(state="normal")
        self.text.insert("end", f"[{who}] ", "who")
        self.text.insert("end", f"{what}\n")
        self.text.configure(state="disabled")
        self.text.see("end")

        self.text.tag_configure("who", foreground=ACCENT)
        self.status.configure(text=f"已发出 {self.index} 条")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.index = 0
        self.status.configure(text="已清空")

    def toggle(self) -> None:
        self.running = not self.running
        self.toggle_btn.configure(text="暂停" if self.running else "继续")
        if self.running:
            self.schedule()

    def schedule(self) -> None:
        if self._job is not None:
            self.root.after_cancel(self._job)
        if self.running:
            self._job = self.root.after(INTERVAL_MS, self._tick)

    def _tick(self) -> None:
        self._job = None
        self.add_one()
        self.schedule()


if __name__ == "__main__":
    root = tk.Tk()
    ChatSim(root)
    root.mainloop()
