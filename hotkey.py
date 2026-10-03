"""全局热键。

用 Windows 的 RegisterHotKey，不用键盘钩子。

原因：pynput 在 Windows 上装的是 WH_KEYBOARD_LL 全局钩子。带内核级反作弊的
网游（EAC / BattlEye / Vanguard / 网易腾讯系）会检测甚至封禁键盘钩子——
而本工具的目标场景恰恰是游戏。RegisterHotKey 是正规系统 API，由系统投递
WM_HOTKEY 消息，无钩子、无注入，反作弊视角下跟一个输入法热键没区别。

RegisterHotKey 还有个额外好处：它不受 UIPI 隔离影响，
即使游戏以管理员权限运行，普通权限的我们也能收到按键。
"""

import ctypes
import threading
from ctypes import wintypes

import winutil

user32 = winutil.user32
kernel32 = winutil.kernel32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # 不加这个，长按会疯狂触发

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
    "super": MOD_WIN,
}

_VK_NAMES = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
    # 符号键（OEM 键位）
    "`": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
    # 小键盘
    **{f"numpad{n}": 0x60 + n for n in range(10)},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}

# 虚拟键码 -> 按键名。vk_to_spec 用这个把 keycode 翻成可读的名字。
VK_TO_SPEC = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x20: "space",
    0x21: "pageup", 0x22: "pagedown", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2D: "insert", 0x2E: "delete",
    0xC0: "`", 0xBD: "-", 0xBB: "=",
    0xDB: "[", 0xDD: "]", 0xDC: "\\",
    0xBA: ";", 0xDE: "'",
    0xBC: ",", 0xBE: ".", 0xBF: "/",
    **{0x60 + n: f"numpad{n}" for n in range(10)},
    **{0x6F + i: f"f{i}" for i in range(1, 13)},
}


def vk_to_spec(vk: int) -> str | None:
    """虚拟键码 -> 按键名。

    比 keysym 可靠得多：keysym 会随键盘布局和输入法状态变化，
    认不出来时直接报 "??"，而且同一个键可能有好几个名字
    （分号可能是 semicolon 也可能就是 ";"，单引号可能是
    apostrophe 也可能是 quoteright）。keycode 没有这些歧义。
    """
    if 0x41 <= vk <= 0x5A:  # A-Z
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:  # 0-9
        return chr(vk)
    return VK_TO_SPEC.get(vk)


# Tk 的 keysym 名 -> 上面那套按键名。
# 字母和数字不走这里（直接取字符本身）。
KEYSYM_TO_SPEC = {
    "BackSpace": "backspace", "Tab": "tab",
    "Return": "enter", "Escape": "esc", "space": "space",
    "Prior": "pageup", "Next": "pagedown",
    "End": "end", "Home": "home",
    "Left": "left", "Up": "up", "Right": "right", "Down": "down",
    "Insert": "insert", "Delete": "delete",
    "grave": "`", "minus": "-", "equal": "=",
    "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "semicolon": ";", "apostrophe": "'", "quoteright": "'",
    "comma": ",", "period": ".", "slash": "/",
    # 小键盘
    "KP_0": "numpad0", "KP_1": "numpad1", "KP_2": "numpad2",
    "KP_3": "numpad3", "KP_4": "numpad4", "KP_5": "numpad5",
    "KP_6": "numpad6", "KP_7": "numpad7", "KP_8": "numpad8",
    "KP_9": "numpad9",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}

# 原型声明
user32.RegisterHotKey.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# 修饰键掩码 -> 通用虚拟键码
MOD_CONTROL_VK = 0x11   # VK_CONTROL
MOD_ALT_VK = 0x12       # VK_MENU
MOD_SHIFT_VK = 0x10     # VK_SHIFT
MOD_WIN_VK = 0x5B       # VK_LWIN

_MOD_VKS = {
    MOD_CONTROL: MOD_CONTROL_VK,
    MOD_ALT: MOD_ALT_VK,
    MOD_SHIFT: MOD_SHIFT_VK,
    MOD_WIN: MOD_WIN_VK,
}


# 低级键盘钩子报的是「左/右分开」的修饰键码，而热键配置里用的是通用码。
# 不归一化的话，Alt/Ctrl/Shift 相关的热键永远匹配不上 —— 钩子给 0xA4(左Alt)，
# 配置里写的是 0x12，两边对不上。
_VK_NORMALIZE = {
    0xA0: MOD_SHIFT_VK, 0xA1: MOD_SHIFT_VK,
    0xA2: MOD_CONTROL_VK, 0xA3: MOD_CONTROL_VK,
    0xA4: MOD_ALT_VK, 0xA5: MOD_ALT_VK,
}


def normalize_vk(vk: int) -> int:
    """把左右分开的修饰键码折回通用码。"""
    return _VK_NORMALIZE.get(vk, vk)


def _vk_variants(vk: int) -> tuple[int, ...]:
    """一个通用码对应的所有实际按键码。

    GetAsyncKeyState(0x12) 不一定覆盖右 Alt，所以三个变体都查一遍。
    """
    extra = {
        MOD_SHIFT_VK: (0xA0, 0xA1),
        MOD_CONTROL_VK: (0xA2, 0xA3),
        MOD_ALT_VK: (0xA4, 0xA5),
    }.get(vk)
    return (vk, *extra) if extra else (vk,)


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'<ctrl>+<alt>+t' -> (修饰键掩码, 虚拟键码)。"""
    mods = 0
    vk = 0

    for raw in spec.lower().split("+"):
        token = raw.strip().strip("<>").strip()
        if not token:
            continue

        if token in _MOD_MAP:
            mods |= _MOD_MAP[token]
        elif token in _VK_NAMES:
            vk = _VK_NAMES[token]
        elif len(token) == 1 and (token.isalnum()):
            vk = ord(token.upper())  # VK_A..VK_Z = 0x41.., VK_0..VK_9 = 0x30..
        else:
            raise ValueError(f"不认识的按键：{token}")

    if vk == 0:
        raise ValueError(f"热键里没有主键：{spec}")
    return mods, vk


class HotkeyConflictError(Exception):
    """热键已被别的程序占用。"""


WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HookHotkey:
    """用低级键盘钩子（WH_KEYBOARD_LL）截获按键。

    三个方案里最底层的一个，也最可能突破游戏屏蔽：

      · RegisterHotKey  —— 向系统登记这个组合，由系统通知。
                          游戏用 DirectInput 抢了更底层的键盘 IO，能把它拦掉。
      · GetAsyncKeyState —— 轮询查询按键状态。理论上是普通查询没理由被拦，
                          但实测在 LOL 里同样收不到。
      · 键盘钩子         —— 直接挂在系统的键盘消息链上，比游戏更靠前，
                          能先看到按键。AutoHotkey 就是靠它（#InstallKeybdHook）
                          在 LOL 里工作的。

    注意事项：
      · 低级键盘钩子必须装在**有消息循环**的线程上 —— 这个类自带一个
      · 回调里不能做耗时操作，系统有超时（默认 300ms），超了会把钩子摘掉
      · 回调函数对象必须一直持有，被 GC 回收掉钩子就哑了
    """

    def __init__(self) -> None:
        self._bindings: list[tuple[frozenset[int], object, str]] = []
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._ok = False
        self._hook = None
        self._proc = None  # 必须留引用，否则回调被回收

    def register(self, hotkey: str, callback) -> None:
        """接口和 HotkeyManager 保持一致，方便互换。"""
        mods, vk = parse_hotkey(hotkey)
        keys = frozenset([v for m, v in _MOD_VKS.items() if mods & m] + [vk])
        self._bindings.append((keys, callback, hotkey))

    def start(self) -> bool:
        if not self._bindings:
            return False
        self._thread = threading.Thread(
            target=self._run, name="hotkey-hook", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=3)
        return self._ok

    def _run(self) -> None:
        user32 = winutil.user32
        kernel32 = winutil.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        # 钩子回调要求消息循环 —— 先给自己建一个
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        held: set[int] = set()

        def _proc(n_code, w_param, l_param):
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                try:
                    kb = ctypes.cast(
                        l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                    ).contents
                    # 必须归一化：钩子给的是 0xA4(左Alt) 这种，配置里是 0x12
                    held.add(normalize_vk(int(kb.vkCode)))
                    for keys, callback, _spec in self._bindings:
                        if keys and keys.issubset(held):
                            try:
                                callback()
                            except Exception:
                                pass
                except Exception:
                    pass
            elif n_code == 0:
                # 松开：从"按住的键"里移除
                try:
                    kb = ctypes.cast(
                        l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                    ).contents
                    held.discard(normalize_vk(int(kb.vkCode)))
                except Exception:
                    pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(_proc)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, None, 0
        )
        self._ok = bool(self._hook)
        self._ready.set()

        if not self._ok:
            return

        # 跑消息循环，钩子才会被投递
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self) -> None:
        if self._thread_id is not None:
            try:
                winutil.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except (AttributeError, OSError):
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._ok = False

    def conflict_message(self) -> str:
        return ""

    def failed_specs(self) -> set:
        return set()  # 钩子模式不向系统登记，不存在「被占用」

    @property
    def started(self) -> bool:
        return self._ok


class PollingHotkey:
    """轮询检测按键，代替 RegisterHotKey。

    为什么需要它：有些游戏（LOL 就是）会把外部程序注册的全局热键整个屏蔽掉，
    RegisterHotKey 一点反应都没有。换成 GetAsyncKeyState 轮询就行了 ——
    它只是查询「系统里这个键现在按着没」，不注册、不挂钩子、不在系统里
    留下任何可被拦截的东西。

    代价：
      · 有一点延迟（一个轮询周期，默认 40ms，基本感觉不到）
      · 不区分前台窗口 —— 在任何地方按都会触发
    """

    def __init__(self, interval: float = 0.04) -> None:
        self._interval = interval
        self._bindings: list[tuple[list[int], object, str]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, hotkey: str, callback) -> None:
        """接口和 HotkeyManager 保持一致，方便两者互换。"""
        mods, vk = parse_hotkey(hotkey)
        keys = [v for m, v in _MOD_VKS.items() if mods & m]
        keys.append(vk)
        self._bindings.append((keys, callback, hotkey))

    def start(self) -> bool:
        if not self._bindings:
            return False
        self._thread = threading.Thread(
            target=self._run, name="hotkey-poll", daemon=True
        )
        self._thread.start()
        return True

    def _run(self) -> None:
        user32 = winutil.user32
        was_down = [False] * len(self._bindings)

        while not self._stop.is_set():
            for i, (keys, callback, _spec) in enumerate(self._bindings):
                try:
                    # 修饰键左右都要看：GetAsyncKeyState(0x12) 只覆盖左 Alt，
                    # 只按右 Alt 就漏了
                    down = all(
                        any(user32.GetAsyncKeyState(v) & 0x8000 for v in _vk_variants(k))
                        for k in keys
                    )
                except OSError:
                    return

                # 只在「刚按下」那一下触发，按住不放不会连发
                if down and not was_down[i]:
                    try:
                        callback()
                    except Exception:
                        pass
                was_down[i] = down

            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def conflict_message(self) -> str:
        return ""  # 轮询模式不存在「被占用」这回事

    def failed_specs(self) -> set:
        return set()

    @property
    def started(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class HotkeyManager:
    """所有热键在同一个线程里注册并跑消息循环 —— RegisterHotKey 要求如此。"""

    def __init__(self) -> None:
        self._bindings: dict[int, tuple[int, int, object, str]] = {}
        self._next_id = 1
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._registered: list[int] = []
        self.conflicts: list[tuple[str, int]] = []
        self.started = False

    # ---------------------------------------------------------- 注册

    def register(self, hotkey: str, callback) -> None:
        mods, vk = parse_hotkey(hotkey)
        self._bindings[self._next_id] = (mods, vk, callback, hotkey)
        self._next_id += 1

    def start(self) -> bool:
        """起线程注册并监听。返回是否有至少一个热键注册成功。"""
        if not self._bindings:
            return False
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        return self.started

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        try:
            for hid, (mods, vk, _cb, spec) in self._bindings.items():
                ok = user32.RegisterHotKey(
                    None, hid, mods | MOD_NOREPEAT, vk
                )
                if ok:
                    self._registered.append(hid)
                else:
                    self.conflicts.append((spec, ctypes.get_last_error()))
            self.started = bool(self._registered)
        finally:
            self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != WM_HOTKEY:
                continue
            entry = self._bindings.get(int(msg.wParam))
            if entry is None:
                continue
            try:
                # 回调必须立即返回：这里做耗时操作会卡住所有热键
                entry[2]()
            except Exception:
                pass

        for hid in self._registered:
            user32.UnregisterHotKey(None, hid)
        self._registered.clear()

    # ---------------------------------------------------------- 停止

    def stop(self) -> None:
        if self._thread_id is not None:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except (AttributeError, OSError):
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.started = False

    def conflict_message(self) -> str:
        if not self.conflicts:
            return ""
        parts = []
        for spec, err in self.conflicts:
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                parts.append(f"{spec} 已被别的程序占用")
            else:
                parts.append(f"{spec} 注册失败（错误码 {err}）")
        return "；".join(parts)

    def failed_specs(self) -> set:
        """没注册上的那些组合键 —— 换热键时要精确知道是哪一个被占了。"""
        return {spec for spec, _err in self.conflicts}


if __name__ == "__main__":
    # 手动验证：python hotkey.py
    import time

    print("解析测试:")
    for spec in ("<ctrl>+<alt>+t", "<ctrl>+<shift>+<f1>", "<alt>+q"):
        print(f"  {spec} -> {parse_hotkey(spec)}")

    hits = {"count": 0}

    def on_hit():
        hits["count"] += 1
        print(f"  热键触发 #{hits['count']}")

    mgr = HotkeyManager()
    mgr.register("<ctrl>+<alt>+t", on_hit)

    # 故意注册一个几乎肯定被占用的组合，验证冲突检测
    mgr.register("<ctrl>+<alt>+delete", on_hit)

    if not mgr.start():
        print("没有任何热键注册成功")
    else:
        print("已注册。请按 Ctrl+Alt+T 触发，长按验证不重复触发。10 秒后退出。")
    if mgr.conflict_message():
        print("冲突:", mgr.conflict_message())

    try:
        time.sleep(10)
    finally:
        mgr.stop()
        print(f"共触发 {hits['count']} 次")
