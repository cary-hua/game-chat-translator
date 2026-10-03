"""系统托盘图标。

pystray 的 run() 是阻塞的，所以跑在独立线程里。
它的菜单回调也在那个线程上 —— 碰 Tk 一律走 app._ui。
"""

import threading

from PIL import Image
from pystray import Icon, Menu, MenuItem

from config import RESOURCE_DIR

# 图标是随包分发的只读资源：打包后它在解压目录里，不在配置旁边
ICON_PATH = RESOURCE_DIR / "assets" / "logo.png"
ICON_SIZE = 64


def _load_icon() -> Image.Image | None:
    """把 logo 处理成托盘能用的方形图标。

    原图是 216x97 的横条，而且图形是纯黑的 —— 直接塞进托盘
    在深色任务栏上会看不见。所以补成正方形并垫上白底。
    """
    try:
        raw = Image.open(ICON_PATH).convert("RGBA")
    except Exception:
        return None

    side = max(raw.width, raw.height)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.paste(
        raw,
        ((side - raw.width) // 2, (side - raw.height) // 2),
        raw,  # 第三参用 alpha 做蒙版
    )
    return canvas.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)


class Tray:
    def __init__(self, app) -> None:
        self.app = app
        self._icon = None
        self._thread = None

    def start(self) -> bool:
        image = _load_icon()
        if image is None:
            return False

        menu = Menu(
            MenuItem("显示主窗口", self._show, default=True),
            MenuItem("翻译聊天栏", self._translate),
            MenuItem("打中文回话（弹小框）", self._reply),
            Menu.SEPARATOR,
            MenuItem("退出", self._quit),
        )

        try:
            self._icon = Icon("game-translator", image, "游戏聊天栏翻译器", menu)
        except Exception:
            return False

        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception:
            pass  # 托盘起不来不影响主功能

    # ------------------------------------------------------ 菜单回调
    # 这些跑在托盘线程上，碰 UI 必须转到主线程

    def _show(self, *_args) -> None:
        self.app._ui(self.app.show_window)

    def _translate(self, *_args) -> None:
        self.app.jobs.put("translate")

    def _reply(self, *_args) -> None:
        # keep_game=True：托盘上点的时候前台是本程序，不能把它当游戏窗口记下来
        self.app._ui(lambda: self.app._on_quick_reply(keep_game=True))

    def _quit(self, *_args) -> None:
        self.app._ui(self.app.shutdown)

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            pass
        self._icon = None
