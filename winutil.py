"""Win32 工具：DPI 感知、窗口扩展样式、屏幕几何、前台进程名。

全部用标准库 ctypes 直接调 user32/kernel32，零新增依赖。
（pywin32-ctypes 只提供 win32api/win32crypt/win32cred，不含 win32gui，别指望它。）
"""

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)  # Win8.1+
except OSError:
    shcore = None

# ---------------------------------------------------------------- 常量

GWL_EXSTYLE = -20

WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+

# 虚拟屏幕的 GetSystemMetrics 索引
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# ---------------------------------------------------------------- 原型声明
# 64 位下不声明 restype 会把 LONG_PTR 截断成 32 位，扩展样式会被写坏。

if hasattr(user32, "GetWindowLongPtrW"):
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]

user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]

user32.GetForegroundWindow.restype = wintypes.HWND

if hasattr(user32, "SetProcessDpiAwarenessContext"):
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]

if shcore is not None:
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]

# 剪贴板相关。
# ⚠️ GlobalAlloc / GlobalLock 返回的是句柄和指针，64 位下不声明 restype
# 会被截断成 32 位，写剪贴板静默失败（返回 False，什么也写不进去）。
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

# 低级键盘钩子（热键的第三条路，游戏屏蔽 RegisterHotKey 时用）
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND,
    wintypes.UINT, wintypes.UINT, wintypes.UINT
]

# 抢前台 + 模拟按键（「自动把译文发进游戏」用）
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.MapVirtualKeyW.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.IsIconic.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

# ---------------------------------------------------------------- DPI


def set_dpi_awareness() -> str:
    """设置进程 DPI 感知。**必须在 import tkinter / 创建任何窗口之前调用。**

    不设的话：高 DPI 缩放下 Tk 报逻辑像素、mss 抓物理像素，
    框选出来的坐标会差一个缩放倍率（150% 下差 1.5 倍），截出来的图完全错位。

    返回实际生效的级别，仅用于日志。
    """
    # Windows 10 1703+: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
        except OSError:
            pass

    # Windows 8.1+: PROCESS_PER_MONITOR_DPI_AWARE == 2
    if shcore is not None:
        try:
            if shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
                return "per-monitor"
        except OSError:
            pass

    # Vista+ 兜底
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass

    return "none"


# ---------------------------------------------------------------- 屏幕几何


def get_virtual_rect() -> tuple[int, int, int, int]:
    """整个虚拟桌面的 (left, top, width, height)。

    多显示器时 left/top 可能是负数（副屏在主屏左边/上边）。
    """
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def get_monitors() -> list[tuple[int, int, int, int]]:
    """每块显示器的 (left, top, width, height)。用于调试和诊断。"""
    monitors = []

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), ctypes.c_double,
    )

    def _cb(hmon, hdc, lprect, data):
        r = lprect.contents
        monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    except OSError:
        pass
    return monitors


# ---------------------------------------------------------------- HWND


def get_hwnd(widget) -> int:
    """Tk widget -> 顶层窗口 HWND。

    winfo_id() 在 Windows 上给的可能是子窗口，往上爬到顶层才是真正要操作的句柄。
    """
    hwnd = int(widget.winfo_id())
    for _ in range(8):  # 防御性上限，正常最多两层
        parent = user32.GetParent(hwnd)
        if not parent:
            return hwnd
        hwnd = parent
    return hwnd


def _get_ex_style(hwnd: int) -> int:
    if hasattr(user32, "GetWindowLongPtrW"):
        return user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return user32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def _set_ex_style(hwnd: int, value: int) -> None:
    if hasattr(user32, "SetWindowLongPtrW"):
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, value)
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, value)


def _refresh_frame(hwnd: int) -> None:
    """改完扩展样式必须走一次 SetWindowPos 才生效。"""
    user32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def add_ex_style(hwnd: int, style: int) -> None:
    _set_ex_style(hwnd, _get_ex_style(hwnd) | style)
    _refresh_frame(hwnd)


def remove_ex_style(hwnd: int, style: int) -> None:
    _set_ex_style(hwnd, _get_ex_style(hwnd) & ~style)
    _refresh_frame(hwnd)


def has_ex_style(hwnd: int, style: int) -> bool:
    return bool(_get_ex_style(hwnd) & style)


# ---------------------------------------------------------------- 窗口行为


def set_topmost(hwnd: int, on: bool = True) -> None:
    """置顶。用 SWP_NOACTIVATE —— 绝不激活窗口，否则会把游戏的焦点抢走。"""
    user32.SetWindowPos(
        hwnd,
        wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
        0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


def move_window(hwnd: int, x: int, y: int, w: int | None = None, h: int | None = None) -> None:
    """移动/缩放窗口，支持负坐标。

    Tk 的 geometry("WxH+X+Y") 里 '-' 表示"距右/下边缘的距离"，
    表达不了左侧副屏那种绝对负坐标，所以定位一律走这里。
    """
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    if w is None or h is None:
        flags |= SWP_NOSIZE
        w = h = 0
    user32.SetWindowPos(hwnd, None, x, y, w, h, flags)


def exclude_from_capture(hwnd: int, on: bool = True) -> bool:
    """让窗口从屏幕捕获中消失（Win10 2004+）。

    一石二鸟：
      1. mss 走 GDI BitBlt，抓不到这个窗口；
      2. 解决一个隐蔽的回环 bug —— 悬浮窗若和聊天框重叠，
         下一次截图会把上一轮的翻译结果当成聊天内容再翻一遍。

    需要窗口已经真实创建（deiconify 之后）才有效。返回是否设置成功。
    """
    try:
        ok = user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE
        )
        return bool(ok)
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------- 前台进程


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def set_clipboard(text: str) -> bool:
    """把文本写进系统剪贴板。

    不用 Tk 的 clipboard_append：那是"拥有者模式"，程序一退出剪贴板就空了。
    这里走 Win32，写完就归系统管，跟程序活不活着没关系。
    """
    try:
        if not user32.OpenClipboard(None):
            return False
    except OSError:
        return False

    handle = None
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)

        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        handle = None  # 所有权交给系统了，不能再自己释放
        return True
    except OSError:
        if handle:
            kernel32.GlobalFree(handle)
        return False
    finally:
        user32.CloseClipboard()


def is_admin() -> bool:
    """当前进程是不是以管理员权限运行。

    这个很重要：如果游戏以管理员权限跑，而本程序是普通权限，
    全局热键、截图、悬浮窗都可能对它失效（权限隔离）。
    """
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_pressed_modifiers() -> list[str]:
    """当前实际按着的修饰键，返回 ['ctrl', 'alt', 'shift'] 的子集（按此顺序）。

    直接问系统，不看 Tk 的 event.state —— Tk 在 Windows 上报告 Alt 用的是
    0x20000 那一位，很容易误判成"Alt 一直按着"，结果每个热键都被强行加上 Alt。
    """
    if not hasattr(user32, "GetAsyncKeyState"):
        return []
    mods = []
    if user32.GetAsyncKeyState(0x11) & 0x8000:  # VK_CONTROL
        mods.append("ctrl")
    if user32.GetAsyncKeyState(0x12) & 0x8000:  # VK_MENU (Alt)
        mods.append("alt")
    if user32.GetAsyncKeyState(0x10) & 0x8000:  # VK_SHIFT
        mods.append("shift")
    return mods


def get_foreground_process_name() -> str:
    """前台窗口的进程名，小写，例如 'notepad.exe'。拿不到返回空串。

    为以后按游戏自动切换 profile 准备（现在只用于调试）。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        # 权限不足（比如前台是管理员进程）就拿不到，属正常情况
        return ""

    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- 抢前台 + 发按键
#
# 这一节是「把译文发进游戏」用的，也是整个程序里唯一会往外动手的地方 ——
# 别处都只读屏幕、只发 HTTP。往游戏里发按键属于自动化特征，理论上存在被反作弊
# 盯上的可能。注意这里只能发按键，不能靠剪贴板：LOL 有意堵了外部粘贴，
# Ctrl+V 不管是模拟的还是你手按的都粘不进去，只能逐字符打。

# SendInput 的 INPUT 结构。
# ⚠️ 64 位下这个 union 里最大的是 MOUSEINPUT（32 字节），不是 KEYBDINPUT（24），
# 按 KEYBDINPUT 算整个结构就短一截，SendInput 会直接返回 0、一个键都发不出去。
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# 能写名字的键。虚拟键和扫描码都填上，兼容性最好 ——
# 只填虚拟键时，个别 DirectInput 系的游戏收不到。
VK_NAMES = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
}
# 字母键**全给上**。以前只列了 a/c/v/x/z 那几个 —— 那是只做 Ctrl+V 时写死的，
# 现在用户能自己指定"我的游戏用 T 开聊天框"，少一个字母就是一次
# 「不认识的键：t」。键名一律小写，因为解析序列时整串先 lower 过。
VK_NAMES.update({chr(c).lower(): c for c in range(0x41, 0x5B)})  # A-Z
VK_NAMES.update({str(d): 0x30 + d for d in range(10)})           # 0-9
VK_NAMES.update({f"f{i}": 0x6F + i for i in range(1, 13)})       # F1-F12

SW_RESTORE = 9


def _key_input(vk: int, up: bool) -> _INPUT:
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    return _INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=_KEYBDINPUT(
            wVk=vk,
            wScan=scan,
            dwFlags=KEYEVENTF_KEYUP if up else 0,
            time=0,
            dwExtraInfo=None,
        )),
    )


def get_foreground_window() -> int:
    """当前前台窗口的 HWND，没有就 0。"""
    return int(user32.GetForegroundWindow() or 0)


def force_foreground(hwnd: int) -> bool:
    """把窗口强行切到前台，返回是否成功。

    Windows 有「前台锁定」：不是当前前台进程的程序调 SetForegroundWindow
    会被静默忽略 —— 返回成功，但窗口没上来。常规绕法是把本线程临时附加到
    当前前台线程上，借它的权限去设置，设完再摘掉。
    """
    if not hwnd:
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True

    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_thread = kernel32.GetCurrentThreadId()
    if fg_thread and fg_thread != my_thread:
        user32.AttachThreadInput(my_thread, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(my_thread, fg_thread, False)

    return user32.GetForegroundWindow() == hwnd


def restore_window(hwnd: int) -> None:
    """窗口要是被最小化了，先还原 —— 最小化的窗口没法成为前台。"""
    if hwnd and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def _unicode_input(unit: int, up: bool) -> _INPUT:
    """一个 Unicode 码元的按键事件（wScan 装字符，不是扫描码）。"""
    return _INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=_KEYBDINPUT(
            wVk=0,
            wScan=unit,
            dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
            time=0,
            dwExtraInfo=None,
        )),
    )


def send_unicode_text(text: str, per_char_ms: int = 6) -> tuple[bool, str]:
    """把文本逐字符「打」出去，不走剪贴板、不按任何修饰键。

    每个字符以 KEYEVENTF_UNICODE 交给系统，由系统翻成 WM_CHAR 给焦点窗口。
    实测 LOL 收单键（回车）但不收 Ctrl+V，这条路绕开了修饰键组合，
    是往游戏聊天框里送字的后备通道。

    中文、日文都在 BMP 内，一个码元一个字符；只有超出 BMP 的（emoji 之类）
    才需要拆成代理对。
    """
    if not text:
        return False, "没有要输入的文本"

    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            units = [code]

        batch: list[_INPUT] = []
        for unit in units:
            batch.append(_unicode_input(unit, up=False))
            batch.append(_unicode_input(unit, up=True))

        arr = (_INPUT * len(batch))(*batch)
        sent = user32.SendInput(len(batch), ctypes.byref(arr), ctypes.sizeof(_INPUT))
        if sent != len(batch):
            err = ctypes.get_last_error()
            if sent == 0 and err == 5:
                return False, "游戏以管理员身份运行，本程序权限不够"
            return False, f"输到「{ch}」就断了（错误码 {err}）"

        if per_char_ms > 0:
            time.sleep(per_char_ms / 1000.0)

    return True, f"已输入 {len(text)} 个字符"


def _send_combo(action: str, repeat: int = 1) -> tuple[bool, str]:
    """发一个动作（`ctrl+v` 这种组合键），可重复 N 次。"""
    keys = [k.strip() for k in action.split("+") if k.strip()]
    for k in keys:
        if k not in VK_NAMES:
            return False, f"不认识的键：{k}"

    for _ in range(repeat):
        batch: list[_INPUT] = []
        # 正序按下、倒序松开 —— Ctrl+V 得 ctrl 先按后松，
        # 反过来就成了「按着 V 敲 Ctrl」
        for k in keys:
            batch.append(_key_input(VK_NAMES[k], up=False))
        for k in reversed(keys):
            batch.append(_key_input(VK_NAMES[k], up=True))

        arr = (_INPUT * len(batch))(*batch)
        sent = user32.SendInput(len(batch), ctypes.byref(arr), ctypes.sizeof(_INPUT))
        if sent != len(batch):
            err = ctypes.get_last_error()
            if sent == 0 and err == 5:
                return False, "游戏以管理员身份运行，本程序权限不够（试试点「以管理员身份运行」重开）"
            return False, f"「{action}」只发出 {sent}/{len(batch)} 个按键（错误码 {err}）"

        if repeat > 1:
            # 连发同一个键得留缝，挨太紧会被当成一次长按合成掉。
            # 8ms 是够用的最小值 —— backspace*100 要清空输入框，别拖太久。
            time.sleep(0.008)

    return True, ""


def send_sequence(spec: str, text: str = "", gap_ms: int = 70) -> tuple[bool, str]:
    """按序列发按键，序列里可以夹一个「把文本打进去」的动作。

    逗号分隔动作，三种写法：
      · `ctrl+v`        —— 组合键
      · `text`          —— 把 text 参数逐字符打进去（不走剪贴板、不按修饰键）
      · `backspace*60`  —— 星号后面是重复次数

    每个动作之间停一下 —— 游戏打开聊天框有动画，回车刚下去就跟着发粘贴，
    粘贴会掉在聊天框出现之前，等于没粘上。
    """
    actions = [a.strip().lower() for a in (spec or "").split(",") if a.strip()]
    if not actions:
        return False, "按键序列是空的"

    for index, action in enumerate(actions):
        if action == "text":
            if not text:
                continue
            ok, msg = send_unicode_text(text)
        else:
            repeat = 1
            if "*" in action:
                key_part, _, times = action.partition("*")
                action = key_part.strip()
                try:
                    repeat = max(1, min(500, int(times)))
                except ValueError:
                    return False, f"重复次数看不懂：{times}"
            ok, msg = _send_combo(action, repeat)

        if not ok:
            return False, msg

        if index < len(actions) - 1:
            time.sleep(max(0, gap_ms) / 1000.0)

    return True, f"已发送 {len(actions)} 个动作"


def send_keys(spec: str, gap_ms: int = 70) -> tuple[bool, str]:
    """只发按键、不带文本的简写。"""
    return send_sequence(spec, "", gap_ms)


if __name__ == "__main__":
    # 手动验证：python winutil.py
    print("DPI 级别:", set_dpi_awareness())
    print("虚拟桌面:", get_virtual_rect())
    print("显示器:", get_monitors())
    print("前台进程:", get_foreground_process_name() or "(拿不到，试试点一下记事本再跑)")
