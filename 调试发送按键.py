"""测「把译文送进游戏」这条路，在你机器上到底哪种发法能通。

背景：Ctrl+Alt+E 回话时，回车能生效、Ctrl+V 却没粘上，聊天框开了却是空的。
单键能进、组合键不进，那就得换个不靠修饰键的送字方式。

这个脚本拿几个本地输入框当靶子，把几种发法各试一遍：

  1. Ctrl+V 粘贴        —— 现在程序用的，靠剪贴板 + 修饰键
  2. 逐字符 Unicode     —— 不碰剪贴板、不按任何修饰键，一个字符一个字符打
  3. 清空再打           —— 游戏内打字那条路：先退格清空原文，再逐字符打新的

哪个框里出现了日文，哪种发法就能用。

用法：
    python 调试发送按键.py

它会抢一次焦点（约 5 秒），跑完自动把焦点还给原来那个窗口 ——
所以你在游戏里也能直接跑，不用先切出去。
"""

import ctypes
import sys
import time
import tkinter as tk

import winutil

VK_CTRL = 0x11
VK_V = 0x56

PAYLOAD = "おつ、jgはtopに来て"
OLD_TEXT = "打野来上路快点"  # 模拟你在游戏聊天框里已经打好的中文


def _key(vk: int, up: bool, scancode_mode: bool = False):
    scan = winutil.user32.MapVirtualKeyW(vk, 0)
    flags = winutil.KEYEVENTF_KEYUP if up else 0
    if scancode_mode:
        flags |= 0x0008  # KEYEVENTF_SCANCODE
    return winutil._INPUT(
        type=winutil.INPUT_KEYBOARD,
        u=winutil._INPUTUNION(ki=winutil._KEYBDINPUT(
            wVk=0 if scancode_mode else vk,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None,
        )),
    )


def _flush(steps, scancode_mode: bool = False) -> int:
    arr = (winutil._INPUT * len(steps))(
        *[_key(vk, up, scancode_mode) for vk, up in steps]
    )
    return winutil.user32.SendInput(
        len(steps), ctypes.byref(arr), ctypes.sizeof(winutil._INPUT)
    )


def send_ctrl_v_scancode() -> str:
    """只用扫描码发 Ctrl+V（DirectInput 系的游戏只认这个）。"""
    total = _flush([(VK_CTRL, False), (VK_V, False), (VK_V, True), (VK_CTRL, True)], True)
    return f"发出 {total} 个事件"


def send_ctrl_v_plain() -> str:
    """虚拟键发 Ctrl+V —— 现在程序里的做法。"""
    total = _flush([(VK_CTRL, False), (VK_V, False), (VK_V, True), (VK_CTRL, True)], False)
    return f"发出 {total} 个事件"


def send_unicode() -> str:
    """逐字符 Unicode 输入，不碰剪贴板。"""
    ok, msg = winutil.send_unicode_text(PAYLOAD)
    return msg if ok else f"失败：{msg}"


def send_clear_then_unicode() -> str:
    """先退格清空，再逐字符打 —— 游戏内打字那条路的完整模拟。"""
    ok, msg = winutil.send_sequence("backspace*40,text", text=PAYLOAD, gap_ms=60)
    return msg if ok else f"失败：{msg}"


TESTS = [
    ("Ctrl+V 粘贴（虚拟键，现有做法）", send_ctrl_v_plain, None),
    ("Ctrl+V 粘贴（扫描码）", send_ctrl_v_scancode, None),
    ("逐字符 Unicode 输入", send_unicode, None),
    ("清空原文再逐字符打", send_clear_then_unicode, OLD_TEXT),
]


def main() -> int:
    winutil.set_dpi_awareness()
    winutil.set_clipboard(PAYLOAD)

    root = tk.Tk()
    root.title("模拟按键靶子 —— 别手动点它")
    root.geometry("640x260+160+160")

    tk.Label(
        root, text=f"目标是让框里出现：{PAYLOAD}",
        font=("Microsoft YaHei UI", 10, "bold"),
    ).pack(anchor="w", padx=10, pady=(8, 6))

    boxes = []
    for name, _fn, prefill in TESTS:
        line = tk.Frame(root)
        line.pack(fill="x", padx=10, pady=3)
        tk.Label(line, text=name, width=26, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        box = tk.Entry(line, font=("Microsoft YaHei UI", 10))
        box.pack(side="left", fill="x", expand=True)
        if prefill:
            box.insert(0, prefill)
        boxes.append(box)

    previous = winutil.get_foreground_window()  # 跑完把焦点还给它
    notes: list[str] = []

    def run() -> None:
        winutil.force_foreground(winutil.get_hwnd(root))
        root.after(350, lambda: step(0))

    def step(index: int) -> None:
        if index >= len(TESTS):
            return finish()
        _name, fn, _prefill = TESTS[index]
        boxes[index].focus_set()
        try:
            notes.append(fn())
        except Exception as exc:
            notes.append(f"异常：{exc}")
            boxes[index].delete(0, "end")
            boxes[index].insert(0, f"<发不出去：{exc}>")
        root.after(900, lambda: step(index + 1))

    def give_back() -> None:
        if previous:
            winutil.force_foreground(previous)
        root.after(300, root.destroy)

    def finish() -> None:
        print()
        print("=" * 66)
        for (name, _fn, _p), box, note in zip(TESTS, boxes, notes):
            got = box.get()
            mark = "✔ 成功" if got == PAYLOAD else "✘ 没进去"
            print(f"  {mark}   {name}")
            print(f"           {note}")
            print(f"           框里是：{got!r}")
        print("=" * 66)
        print()
        if any(b.get() == PAYLOAD for b in boxes):
            print("有 ✔ 就说明这条路能用，把程序改成那种发法即可。")
        else:
            print("全 ✘。两种可能：① 靶子窗口不吃合成输入；② 发送本身没生效。")
        print("重点看第 3、4 项 —— 那是不依赖 Ctrl 组合键的路子。")
        root.after(200, give_back)

    root.after(400, run)
    root.mainloop()
    print("（已把焦点还给原来的窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
