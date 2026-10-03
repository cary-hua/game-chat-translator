"""入口：可视化控制面板 + 线程编排。

线程模型（严格分层，破坏任何一条都会出难查的 bug）：

  主线程      tkinter mainloop，所有 widget 操作都在这里
  热键线程    收 WM_HOTKEY，只往队列塞东西，绝不做耗时操作
  worker 线程 截图 + HTTP 请求，绝不碰任何 Tk 对象
  UI 泵      主线程的 after 循环，把 worker 的结果投到 widget 上
"""

import ctypes
import difflib
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

import winutil
from cache import TranslationCache
from capture import CaptureError, ScreenCapture, images_unchanged
from config import (
    DEFAULT_REPLY_LANG,
    HOTKEY_DEFAULTS,
    HOTKEY_LABELS,
    PROJECT_DIR,
    ConfigStore,
    resolve_api_key,
)
from gui import ApiKeyDialog, MainWindow
from hotkey import HookHotkey, HotkeyManager, PollingHotkey, parse_hotkey
from overlay import OverlayWindow
from quick_input import QuickInput
from selector import RegionSelector
from tray import Tray
from translate import (
    Delta,
    Done,
    Failed,
    TranslateError,
    TranslateRequest,
    format_chat_items,
    get_provider,
    parse_chat_json,
    verify_api_key,
)

UI_POLL_MS = 50


class App:
    def __init__(self) -> None:
        self.store = ConfigStore()
        self.cfg = self.store.load()
        self.api_key = resolve_api_key(self.cfg)

        self.root = tk.Tk()
        # ⚠️ 先藏起来。窗口在 Tk() 一创建就是可见的，而接下来
        # MainWindow._build() 里的 center() 要把六个页面轮流 pack 一遍来量
        # 高度，每 pack 一次就真重绘一次 —— 用户看到的是窗口连着变形好几轮。
        # 等界面全部搭好，再在 start() 里露脸。
        self.root.withdraw()

        self.overlay = OverlayWindow(self.root, self.cfg.overlay)
        self.overlay.on_moved = self._on_overlay_moved
        self.selector = RegionSelector(self.root)
        self.quick_input = QuickInput(
            self.root, self._quick_submit, self._quick_confirm
        )
        self._inline_busy = False  # 就地翻译的节流，防连按发两条
        self._watch_on = False  # 监视模式（自动翻新消息）开着没
        self._watch_job = None  # 心跳的 after id，关的时候要掐掉
        # 按热键那一刻的前台窗口 —— 就是游戏。发按键时要还给它。
        # ⚠️ 必须在弹小框之前记下来，小框一出来前台就变了。
        self._game_hwnd = 0

        self.capture = ScreenCapture(self.cfg.capture)
        self.cache = TranslationCache()
        self.translator = None

        self.jobs: queue.Queue = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None

        self.hotkeys = self._make_hotkey_manager()
        self.window = MainWindow(self)
        self.tray = Tray(self)

        self._cancel: threading.Event | None = None
        self._shutting_down = False
        self._stats = {"requests": 0, "cache_hits": 0, "skipped": 0}
        self._last_lines: list[str] = []  # 上次翻译过的行，用来只显示新增的
        self._last_image = None  # 上次的截图，用来判断画面到底变没变
        # 上次识别到的整批条目，用来算"这次新增了哪些"。
        # 不能只记"哪些内容见过" —— 同一个人连发两条一样的话
        # （比如连打两个 gg），第二条会被当成见过的吃掉，显示"没有新消息"。
        self._last_items: list = []

    # ---------------------------------------------------------- 启动

    def start(self) -> None:
        if self.api_key:
            try:
                self.translator = get_provider(self.cfg, self.api_key)
            except TranslateError as exc:
                self.window.set_status(f"翻译后端初始化失败：{exc}", "error")

        if self._register_hotkeys():
            failed = self.hotkeys.failed_specs()
            if failed:
                # 有能用的、也有没注册上的 —— 说清楚是哪个，别让人以为是好的
                self.window.set_hotkey_note(
                    "⚠ 这几个没注册上（多半被别的程序占了）："
                    + "、".join(sorted(failed))
                    + "。点行尾的「修改」换一个。",
                    "error",
                )
            else:
                self.window.set_hotkey_note("✔ 六个热键都已注册", "ok")
        else:
            conflict = self.hotkeys.conflict_message() or "未知原因"
            self.window.set_hotkey_note(
                f"✘ 热键注册失败（{conflict}）。点上面的「修改」换一个组合键。",
                "error",
            )

        if self.tray.start():
            pass  # 托盘起来就行，不用提示

        self.worker = threading.Thread(
            target=self._worker_loop, name="worker", daemon=True
        )
        self.worker.start()

        self.root.after(UI_POLL_MS, self._pump)

        self._refresh_ready_state()
        self._log_startup()

        # 界面全部就位了才露脸（__init__ 里是 withdraw 着的）
        self.window.show()

        # 上次关程序时监视模式是开着的，接着盯
        if self.cfg.profile().watch_mode:
            self._watch_on = True
            self.root.after(800, self._watch_tick)

        # 首次使用：先要 key，再要区域
        if not self.api_key:
            self.root.after(400, self._prompt_api_key)
        elif self.cfg.profile().rect is None:
            self.root.after(400, self._start_select)

        self.root.mainloop()

    def _refresh_ready_state(self) -> None:
        if not self.api_key:
            self.window.set_status("还没填 API key", "error")
        elif self.cfg.profile().rect is None:
            self.window.set_status("还没框选聊天栏区域", "error")
        else:
            self.window.set_status("就绪 —— 可以最小化到后台，按热键翻译", "ok")

    # ---------------------------------------------------------- 首次设置

    def _prompt_api_key(self) -> None:
        def submitted(key):
            if key:
                self.window.api_var.set(key)
                self.save_api_key(key)
            if self.cfg.profile().rect is None:
                self.root.after(700, self._start_select)

        ApiKeyDialog(self.root, submitted)

    def save_api_key(self, key: str, base: str = "", model: str = "") -> None:
        if not key:
            self.window.set_api_hint("key 不能为空", "error")
            return

        self.cfg.api_key = key
        self.api_key = key
        # 接口地址和模型名留空就用原来的 —— 只想换 key 的人不用管这两栏
        if base:
            self.cfg.api_base = base.rstrip("/")
        if model:
            self.cfg.profile().model = model

        try:
            self.store.save(self.cfg)
        except OSError as exc:
            self.window.set_api_hint(f"保存失败：{exc}", "error")
            return

        self.window.set_api_hint("已保存，正在验证…", "muted")
        self.window.set_verify_busy(True)
        threading.Thread(
            target=self._verify_key, args=(key,), name="verify", daemon=True
        ).start()

    def _verify_key(self, key: str) -> None:
        ok, message = verify_api_key(
            key, self.cfg.api_base, self.cfg.profile().model
        )

        def apply():
            self.window.set_verify_busy(False)
            if ok:
                self.window.set_api_hint(f"✔ {message}", "ok")
                try:
                    self.translator = get_provider(self.cfg, self.api_key)
                except TranslateError as exc:
                    self.window.set_api_hint(f"✘ {exc}", "error")
                    return
                self._refresh_ready_state()
            else:
                self.window.set_api_hint(f"✘ {message}", "error")

        self._ui(apply)

    # ---------------------------------------------------------- 界面动作

    def begin_select(self) -> None:
        self._start_select("rect")

    def begin_select_input(self) -> None:
        """框选「聊天输入框」区域 —— 你打字的那一条。"""
        self._start_select("input_rect")

    # ---------------------------------------------------------- 热键

    def _make_hotkey_manager(self):
        """按配置选热键实现。

        poll 是给「游戏屏蔽了 RegisterHotKey」这种情况准备的，见 config.py。
        """
        if self.cfg.hotkey_mode == "hook":
            return HookHotkey()
        if self.cfg.hotkey_mode == "poll":
            return PollingHotkey()
        return HotkeyManager()

    def set_hotkey_mode(self, mode: str) -> None:
        """切换热键工作方式，立即重建。"""
        self.cfg.hotkey_mode = mode
        try:
            self.store.save(self.cfg)
        except OSError:
            pass

        # 同步界面上的单选钮（StringVar.set 不会反过来触发 command，安全）
        if hasattr(self.window, "hotkey_mode_var"):
            try:
                self.window.hotkey_mode_var.set(mode)
            except tk.TclError:
                pass

        self.hotkeys.stop()
        self.hotkeys = self._make_hotkey_manager()

        label = {"hook": "键盘钩子", "poll": "轮询"}.get(mode, "标准")
        if self._register_hotkeys():
            self.window.set_hotkey_note(f"✔ 已注册（{label}方式）", "ok")
            self.window.set_status(f"热键已切换成{label}方式", "ok")
        else:
            self.window.set_hotkey_note(f"✘ {label}方式下注册失败", "error")
            self.window.set_status("热键切换失败", "error")

    def _register_hotkeys(self) -> bool:
        """注册全部热键，返回是否至少成功了一个。

        六个键全部来自 Profile，界面上都能改。
        """
        profile = self.cfg.profile()
        handlers = {
            "hotkey": self._on_translate,
            "hotkey_reply": self._on_quick_reply,
            "hotkey_inline": self._on_inline_reply,
            "hotkey_reselect": self._on_reselect,
            "hotkey_window": self._on_toggle_window,
            "hotkey_quit": self._on_quit,
        }

        # 配置被手改过、两个功能撞了同一个键的话，后来那个退回默认 ——
        # 否则 RegisterHotKey 是先到先得，撞上的那个会静默失效。
        seen: set[str] = set()
        for field, handler in handlers.items():
            spec = getattr(profile, field, "") or HOTKEY_DEFAULTS[field]
            if spec in seen:
                spec = HOTKEY_DEFAULTS[field]
            seen.add(spec)
            self.hotkeys.register(spec, handler)

        return self.hotkeys.start()

    def apply_hotkey(self, field: str, spec: str) -> None:
        """换一个热键：验格式 → 查重 → 存盘 → 全量重建 → 不成器就回退。

        field 是 Profile 上的字段名（hotkey / hotkey_reply / …），
        六个热键走的是同一条路。
        """
        if field not in HOTKEY_DEFAULTS:
            return
        label = HOTKEY_LABELS[field]
        profile = self.cfg.profile()

        try:
            parse_hotkey(spec)
        except ValueError as exc:
            self.window.set_status(f"热键格式不对：{exc}", "error")
            self.window.refresh_hotkeys()
            return

        taken = next(
            (
                HOTKEY_LABELS[f]
                for f in HOTKEY_DEFAULTS
                if f != field and getattr(profile, f, "") == spec
            ),
            None,
        )
        if taken:
            self.window.set_status(f"{spec} 已经是「{taken}」在用，换一个", "error")
            self.window.refresh_hotkeys()
            return

        if self._try_hotkey(profile, field, spec):
            self.window.set_hotkey_note(f"✔ {label}：{spec}", "ok")
            self.window.set_status(f"「{label}」已改成 {spec}", "ok")
            self.window.refresh_hotkeys()
            return

        # 用不了（多半被别的程序占了）：回退到默认，别让人没键可用
        conflict = self.hotkeys.conflict_message() or "未知原因"
        fallback = HOTKEY_DEFAULTS[field]
        if self._try_hotkey(profile, field, fallback):
            self.window.set_hotkey_note(
                f"✘ {spec} 用不了（{conflict}），{label}已回退到 {fallback}", "error"
            )
            self.window.set_status("换的热键被占用，已回退到默认", "error")
        else:
            self.window.set_hotkey_note(
                "✘ 连默认热键都注册不上，可能被别的程序全占了", "error"
            )
            self.window.set_status("热键不可用", "error")
        self.window.refresh_hotkeys()

    def _try_hotkey(self, profile, field: str, spec: str) -> bool:
        """写进配置、重建全部热键，返回这一个是不是真的注册上了。

        每次都要整体重建：RegisterHotKey 按组合登记，光加新的不撤旧的
        会跟自己撞车。成本也就是停线程再起一次，几十毫秒。
        """
        setattr(profile, field, spec)
        try:
            self.store.save(self.cfg)
        except OSError as exc:
            self.window.set_status(f"保存热键失败：{exc}", "error")

        self.hotkeys.stop()
        self.hotkeys = self._make_hotkey_manager()
        self._register_hotkeys()

        if not self.hotkeys.started:
            return False
        return spec not in self.hotkeys.failed_specs()

    def _append_history(self, text: str) -> None:
        """把译文追加到 history.jsonl，方便回看上一句说了什么。"""
        if not text.strip():
            return
        try:
            path = PROJECT_DIR / "history.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "text": text,
                }, ensure_ascii=False) + "\n")

            # 长太大就砍掉前一半，不然会无限涨
            if path.stat().st_size > 1_000_000:
                lines = path.read_text(encoding="utf-8").splitlines()
                path.write_text(
                    "\n".join(lines[len(lines) // 2:]) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass

    def open_history(self) -> None:
        path = PROJECT_DIR / "history.jsonl"
        if not path.exists():
            self.window.set_status("还没有翻译记录", "error")
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.window.set_status(f"打不开历史文件：{exc}", "error")

    def set_skip_unchanged(self, on: bool) -> None:
        """开关「画面没变就跳过」。半透明聊天框的游戏建议关掉。"""
        self.cfg.skip_unchanged = on
        if not on:
            self._last_image = None  # 关掉时清掉参照，免得下次开时误判
        try:
            self.store.save(self.cfg)
        except OSError:
            pass

    # ---------------------------------------------------------- 监视模式

    def set_watch(self, on: bool, interval_ms: int = 0) -> None:
        """开/关监视模式：不按热键，隔一会儿自己看一眼聊天栏。

        只在"聊天框不透明、底下画面不动"的游戏上成立 —— 判据是整块像素
        对比，半透明聊天框会被背景运动骗到。所以默认关，开了也要说清楚。
        """
        profile = self.cfg.profile()
        profile.watch_mode = bool(on)
        if interval_ms:
            profile.watch_interval_ms = max(600, int(interval_ms))

        if on:
            # 监视模式省不省 token 全看「画面没变就跳过」：它要是关着，
            # 那就成了每隔一秒实打实烧一次 API。所以开监视时顺手打开它。
            if not self.cfg.skip_unchanged:
                self.cfg.skip_unchanged = True
                self.window.set_skip_var(True)
            self._watch_on = True
            self._cancel_watch_job()
            self._watch_tick()
        else:
            self._watch_on = False
            self._cancel_watch_job()

        try:
            self.store.save(self.cfg)
        except OSError as exc:
            self.window.set_status(f"保存失败：{exc}", "error")
            return
        self.window.set_status(
            f"✔ 监视中 —— 每 {profile.watch_interval_ms / 1000:.1f} 秒看一眼聊天栏"
            if on else "✔ 已停止监视，改回按热键",
            "ok",
        )

    def _cancel_watch_job(self) -> None:
        """掐掉已排的那次心跳。

        不掐的话，快速开关会跑出两条链：关掉时那次已经排进 Tk 的队列了，
        等它触发时 _watch_on 又变回 True —— 于是它接着排下一次，
        两条链并存，心跳变成双倍速。
        """
        if self._watch_job is None:
            return
        try:
            self.root.after_cancel(self._watch_job)
        except (tk.TclError, ValueError):
            pass
        self._watch_job = None

    def _watch_tick(self) -> None:
        """监视模式的心跳。跑在主线程（root.after），只往队列丢活儿。

        不直接干活是因为截图 + 请求得在 worker 线程里做；
        而且「画面没变就跳过」那套逻辑已经在 _run_job 里了，直接复用。
        """
        self._watch_job = None
        if not self._watch_on or self._shutting_down:
            return

        # 上一轮还没翻完就别排队 —— 否则卡一下会积一串，
        # 等它缓过来会连着翻好几次同样的画面
        if self.jobs.empty():
            self.jobs.put("translate")

        interval = max(600, self.cfg.profile().watch_interval_ms)
        self._watch_job = self.root.after(interval, self._watch_tick)

    def active_entries(self) -> list:
        """当前激活分类下的术语条目。

        分类之间互斥 —— 只取 active_glossary 指的那个。
        同一个词在不同游戏里意思可能不一样，两套同时生效会打架。
        """
        profile = self.cfg.profile()
        groups = profile.glossaries or []
        for group in groups:
            if group.get("name") == profile.active_glossary:
                return group.get("entries") or []
        return groups[0].get("entries") or [] if groups else []

    def set_translate_names(self, on: bool) -> None:
        """开关「翻译说话人的名字」。"""
        self.cfg.translate_names = on
        try:
            self.store.save(self.cfg)
        except OSError:
            pass

        # 同步界面勾选框（StringVar/BooleanVar.set 不会反过来触发 command）
        if hasattr(self.window, "names_var"):
            try:
                self.window.names_var.set(on)
            except tk.TclError:
                pass

    def save_glossaries(self, groups: list, active: str) -> None:
        """保存所有分类 + 当前用哪一个。

        条目会进翻译缓存的键，所以改完旧缓存自动失效，不用手动清。
        """
        profile = self.cfg.profile()
        profile.glossaries = groups
        profile.active_glossary = active

        try:
            self.store.save(self.cfg)
        except OSError as exc:
            self.window.set_glossary_hint(f"保存失败：{exc}", "error")
            return

        entries = self.active_entries()
        kept = sum(1 for e in entries if not e.get("translate", True))
        self.window.set_glossary_hint(
            f"✔ 已保存「{active}」：{len(entries)} 条，其中 {kept} 条保持英文",
            "ok",
        )
        self.window.refresh_glossary()

    def reply_to_foreign(self, text: str) -> None:
        """把中文翻成对方语言（日服=日文），放进剪贴板 —— 用户自己到游戏里粘贴。"""
        if self.translator is None:
            self.window.set_reply_hint("翻译后端没准备好", "error")
            return

        self.window.set_reply_busy(True)
        self.window.set_reply_hint("翻译中…", "muted")
        threading.Thread(
            target=self._reply_worker, args=(text,), name="reply", daemon=True
        ).start()

    def _reply_worker(self, text: str) -> None:
        # 目标语言跟着配置走。术语表倒过来用 ——
        # 表是给「看别人的话」建的，回话时同一张表反着查正合适。
        target = self.cfg.profile().reply_lang or DEFAULT_REPLY_LANG
        try:
            result = self.translator.translate_text(
                text, target=target, glossary=self.active_entries()
            )
            translated = (result.text or "").strip()
        except TranslateError as exc:
            self._ui(lambda e=exc: (
                self.window.set_reply_busy(False),
                self.window.set_reply_hint(f"失败：{e}", "error"),
            ))
            return
        except Exception as exc:
            self._ui(lambda e=exc: (
                self.window.set_reply_busy(False),
                self.window.set_reply_hint(f"出错了：{e}", "error"),
            ))
            return

        def apply() -> None:
            self.window.set_reply_busy(False)
            if not translated:
                self.window.set_reply_hint("没翻出内容，再试一次", "error")
                return
            if self._copy_to_clipboard(translated):
                self.window.set_reply_hint("✔ 已复制到剪贴板", "ok")
            else:
                self.window.set_reply_hint(
                    "复制失败 —— 结果在下面，手动选中复制", "error"
                )
            self.window.show_reply_result(translated)

        self._ui(apply)

    def _on_overlay_moved(self, _pos) -> None:
        """悬浮窗被拖过之后记住新位置，下次翻译还在老地方出现。"""
        try:
            self.store.save(self.cfg)
        except OSError:
            pass

    def _screen_unchanged(self, frame) -> bool:
        """画面和上次比基本没动。

        注意：无论结果如何都要把 _last_image 更新掉 —— 提前 return 的
        那条路径走不到后面的更新逻辑，不在这里更新的话参照物就永远停在上上次。
        """
        previous = self._last_image
        self._last_image = frame.image
        return images_unchanged(previous, frame.image)

    @staticmethod
    def _item_key(entry: dict) -> tuple:
        return (entry.get("user", ""), entry.get("source", ""))

    def _fresh_items(self, items: list) -> list:
        """从这一批条目里挑出相对上次真正新增的。

        做法是拿"上次那一批"和"这一批"做序列比对，找多出来的部分 ——
        而不是逐条判断"这条见过没有"。

        为什么必须这样：同一个人连发两条一样的话（比如连打两个 gg），
        逐条判断会把第二条当成见过的吃掉，用户看到的就是"没有新消息"。
        序列比对能看出"这个位置多了一个"。
        """
        if not items:
            return []

        previous = self._last_items
        self._last_items = [dict(e) for e in items]

        if not previous:
            return items  # 第一次，全都算新的

        matcher = difflib.SequenceMatcher(
            None,
            [self._item_key(e) for e in previous],
            [self._item_key(e) for e in items],
        )

        fresh = []
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                fresh.extend(items[j1:j2])
        return fresh

    def _present(self, frame, result, meta: str) -> str:
        """决定往悬浮窗里显示什么，返回实际显示的文本（给日志用）。

        优先走结构化路径：按原文去重，可靠。
        模型没按格式返回时，退回按译文行对比 —— 不如前者准，但总比不显示强。
        """
        items = parse_chat_json(result.text)

        if items:
            for item in items:
                # 模型偶尔漏给译文，退回原文总比显示空白强
                if not item.get("translation"):
                    item["translation"] = item.get("source", "")

            fresh = self._fresh_items(items)
            if not fresh:
                # 注意这和"画面没变跳过了"不是一回事：这次是真调用过模型的
                self.overlay.set_result(
                    "（没有新消息）", f"{meta} · 已翻译，没新内容")
                return self.overlay.get_text()

            shown = format_chat_items(fresh)
            self.overlay.set_result(shown, meta)
            self._append_history(shown)
            return self.overlay.get_text()

        # 兜底：模型没按 JSON 格式返回，退回按译文行对比
        shown, has_new = self._new_lines_only(result.text)
        if has_new:
            self.overlay.set_result(shown, meta)
            self._append_history(shown)
        else:
            self.overlay.set_result(
                "（没有新消息）", f"{meta} · 已翻译，没新内容")
        return self.overlay.get_text()

    def _new_lines_only(self, text: str) -> tuple[str, bool]:
        """只保留相对上次新增的行，返回 (要显示的内容, 有没有新东西)。

        聊天栏是滚动的：每来一条新消息，整块截图就变了。要是每次都把整块
        译文重显示一遍，旧消息会被反复展示 —— 看起来就像"翻译结果重复了"。
        """
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            return text, True

        if not self._last_lines:
            # 第一次，全部当新的
            self._last_lines = lines
            return text, True

        added: list[str] = []
        matcher = difflib.SequenceMatcher(None, self._last_lines, lines)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                added.extend(lines[j1:j2])

        self._last_lines = lines

        if not added:
            return "", False
        return "\n".join(added), True

    def _log_result(self, frame, result, shown_text: str) -> None:
        """把每次翻译的输入输出记到 captures/results.jsonl。

        只有设了 GCT_SAVE_CAPTURE 才记。
        光有截图不够 —— 要判断"译文为什么重复"，得同时看三样东西：
          1. 截图（模型看到了什么）
          2. result.text（模型返回了什么）
          3. shown_text（悬浮窗实际显示了什么）
        1 和 2 对不上 = 模型的问题；2 和 3 对不上 = 显示层的问题。
        """
        if not os.environ.get("GCT_SAVE_CAPTURE"):
            return
        try:
            log_dir = PROJECT_DIR / "captures"
            log_dir.mkdir(exist_ok=True)
            record = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "shot": frame.content_hash[:8],
                "latency_ms": result.latency_ms,
                "tokens": (result.usage or {}).get("completion_tokens"),
                "reasoning_only": result.reasoning_only,
                "incomplete": result.incomplete,
                "model_text": result.text,
                "shown_text": shown_text,
                # shown_text 是 "译文 + 空行 + 耗时信息"，所以只能比前缀
                "match": shown_text.strip().startswith(result.text.strip()),
            }
            with open(log_dir / "results.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _hide_overlay_for_capture(self, rect) -> None:
        """悬浮窗压到截图区域上时，先把它藏起来再截。

        正常情况不需要：WDA_EXCLUDEFROMCAPTURE 实测对 mss 的 GDI 捕获是生效的
        （窗口明明可见，截图里却找不到它）。
        留这一手是防它在某个游戏/全屏模式下失效 —— 那样上一轮的译文会被
        当成聊天内容再翻一遍，表现就是译文凭空重复。
        只在重叠时才藏，避免无谓的闪烁。
        """
        if not self.overlay.is_visible():
            return

        win = self.overlay.window_rect()
        if win is None:
            return

        wx, wy, ww, wh = win
        overlap = (
            wx < rect.left + rect.width
            and wx + ww > rect.left
            and wy < rect.top + rect.height
            and wy + wh > rect.top
        )
        if not overlap:
            return

        done = threading.Event()

        def _hide() -> None:
            self.overlay.hide()
            done.set()

        self._ui(_hide)
        done.wait(timeout=1.0)

    def set_overlay_mode(self, mode: str) -> None:
        labels = {
            "side": "贴在聊天栏旁边",
            "cover": "覆盖在原位",
            "corner": "固定在屏幕角落",
        }
        self.cfg.overlay.mode = mode
        # 换模式 = 要求按新规则重新摆位，之前手动拖的位置就此作废。
        # 不清掉的话 last_pos 的优先级比模式高，点了等于没点。
        self.cfg.overlay.last_pos = None
        try:
            self.store.save(self.cfg)
        except OSError:
            pass

        self.overlay.set_anchor(self.cfg.profile().rect)
        self.overlay.apply_config(self.cfg.overlay)

        # 立刻摆出来给用户看 —— 窗口没显示时切模式是看不到任何反应的
        self.overlay.set_result(
            f"已切换到「{labels.get(mode, mode)}」\n\n"
            f"以后翻译结果就出现在这个位置。",
            "位置预览 · 想微调直接拖这个窗口",
        )

    def hide_window(self) -> None:
        self.overlay.set_result(
            "程序在后台运行中。\n\n"
            "Ctrl+Alt+T　翻译聊天栏\n"
            "Ctrl+Alt+H　唤回这个窗口\n"
            "Ctrl+Alt+Q　退出",
            "关掉窗口不会退出程序",
        )
        self.root.withdraw()

    def show_window(self) -> None:
        self.window.show()

    def on_window_close(self) -> None:
        self.shutdown()

    # ---------------------------------------------------------- 热键回调
    # 全部跑在热键线程上，只做入队，绝不碰 UI。

    def _debug_log(self, message: str) -> None:
        """GCT_HOTKEY_LOG 开着就把诊断信息记到 hotkey.log。"""
        if not os.environ.get("GCT_HOTKEY_LOG"):
            return
        try:
            with open(PROJECT_DIR / "hotkey.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        except OSError:
            pass

    def _log_startup(self) -> None:
        """把权限和热键模式记进日志 —— 排查『游戏里热键失效』时先看这两行。"""
        self._debug_log(
            f"启动：权限={'管理员' if winutil.is_admin() else '普通用户'}"
            f"，热键模式={self.cfg.hotkey_mode}"
            f"，热键={self.cfg.profile().hotkey}"
        )

    def _on_translate(self) -> None:
        # 用来确认游戏里按热键时，消息到底有没有送到这个进程
        self._debug_log("热键触发")
        self.jobs.put("translate")

    def _on_reselect(self) -> None:
        self._ui(self._start_select)

    def _on_quick_reply(self, keep_game: bool = False) -> None:
        """热键：弹出小输入框，打中文回车就发出去。

        记前台窗口必须赶在弹框前面 —— 小框一抢焦点，前台就不是游戏了。
        从托盘点进来时（keep_game=True）前台是本程序，不能当成游戏，
        沿用上次热键记下的那个窗口。
        """
        if not keep_game:
            self._game_hwnd = winutil.get_foreground_window()
        self._ui(self.quick_input.open)

    def _on_inline_reply(self) -> None:
        """热键：你已经在游戏聊天框里打了中文 —— 就地读出来翻译、替换掉。"""
        if self._inline_busy:
            return  # 上一次还没跑完，别排队重复发
        self._inline_busy = True
        self._game_hwnd = winutil.get_foreground_window()
        self.jobs.put("inline")

    # ---------------------------------------------------------- 小框回话

    def _quick_submit(self, text: str) -> None:
        """小框里按了回车。"""
        if self.translator is None:
            self.quick_input.set_busy(False)
            self.quick_input.set_status("翻译后端没准备好", "error")
            return
        # 记下这次是哪一回 —— 翻译回来时对不上号说明你已经把框关了
        token = self.quick_input.token()
        threading.Thread(
            target=self._quick_worker, args=(text, token), name="quickreply", daemon=True
        ).start()

    def _quick_worker(self, text: str, token: int) -> None:
        profile = self.cfg.profile()
        target = profile.reply_lang or DEFAULT_REPLY_LANG
        try:
            result = self.translator.translate_text(
                text, target=target, glossary=self.active_entries()
            )
            translated = (result.text or "").strip()
        except TranslateError as exc:
            self._ui(lambda e=exc: (
                self.quick_input.set_busy(False),
                self.quick_input.set_status(f"翻译失败：{e}", "error"),
            ))
            return
        except Exception as exc:
            self._ui(lambda e=exc: (
                self.quick_input.set_busy(False),
                self.quick_input.set_status(f"出错了：{exc}", "error"),
            ))
            return

        def apply() -> None:
            if self.quick_input.token() != token:
                return  # 你已经按 Esc 把框关了，这次的结果作废
            if not translated:
                self.quick_input.set_busy(False)
                self.quick_input.set_status("没翻出内容，再按一次回车", "error")
                return

            # 顺手塞进剪贴板。粘贴在游戏里没用（LOL 有意堵了外部粘贴），
            # 但你想粘到别处找人问问也方便，成本几乎为零。
            self._copy_to_clipboard(translated)

            if profile.send_mode == "confirm":
                # 摆回框里等你点头，先别发
                self.quick_input.show_confirm(translated)
                return

            if profile.send_mode == "clipboard":
                # 只复制，不往游戏里发按键
                self.quick_input.set_busy(False)
                self.quick_input.focus_back()
                self.quick_input.set_status(
                    "✔ 已复制到剪贴板，去游戏里粘一下", "ok"
                )
                return

            # 关掉小框再把焦点还给游戏。关闭和前台切换都要时间，所以缓一拍。
            self.quick_input.close()
            self.root.after(140, lambda: self._send_to_game(
                profile.send_seq_quick, translated
            ))

        self._ui(apply)

    def _quick_confirm(self, text: str) -> None:
        """小框里看过译文、又按了一次回车 —— 这回真发。"""
        self.quick_input.close()
        self.root.after(140, lambda: self._send_to_game(
            self.cfg.profile().send_seq_quick, text
        ))

    def _copy_to_clipboard(self, text: str) -> bool:
        """写剪贴板。Win32 被别的程序占着时退回 Tk 的方式。"""
        if winutil.set_clipboard(text):
            return True
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            return True
        except tk.TclError:
            return False

    def _send_to_game(self, seq: str, what: str = "") -> None:
        """把焦点切回游戏，按序列发按键。跑在 Tk 主线程上 —— 窗口是它建的，
        AttachThreadInput 附加的也是它的输入队列。"""
        hwnd = self._game_hwnd
        if not hwnd:
            self.window.set_status(
                "不知道发给哪个窗口 —— 热键要在游戏里按，译文已在剪贴板", "error"
            )
            return

        winutil.restore_window(hwnd)
        if not winutil.force_foreground(hwnd):
            self.window.set_status(
                "切不回游戏窗口，译文在剪贴板里，手动粘一下", "error"
            )
            return

        # 前台切换不是瞬间生效的，立刻发按键会打到还没退位的窗口上
        self.root.after(120, lambda: self._fire_keys(seq, what))

    def _fire_keys(self, seq: str, what: str = "") -> None:
        # what 就是译文 —— 序列里的 `text` 动作会把它逐字符打进去
        ok, msg = winutil.send_sequence(seq, what)
        if ok:
            self.window.set_status(f"✔ 已发出：{what}" if what else "✔ 已发出", "ok")
        else:
            self.window.set_status(f"发送失败：{msg}（译文在剪贴板里）", "error")

    def _on_toggle_window(self) -> None:
        self._ui(self._toggle_window)

    def _toggle_window(self) -> None:
        try:
            if self.root.state() == "withdrawn":
                self.show_window()
            else:
                self.hide_window()
        except tk.TclError:
            pass

    def _on_quit(self) -> None:
        self._ui(self.shutdown)

    # ---------------------------------------------------------- UI 泵

    def _ui(self, fn) -> None:
        """任何线程都可以调，把回调转到主线程执行。"""
        self.ui_queue.put(fn)

    def _pump(self) -> None:
        drained = 0
        while drained < 50:  # 上限防止洪泛饿死主循环
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:  # 回调抛异常绝不能卡死泵
                print(f"UI 回调出错：{exc}", file=sys.stderr)
            drained += 1

        if not self._shutting_down:
            self.root.after(UI_POLL_MS, self._pump)

    # ---------------------------------------------------------- worker

    def _worker_loop(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._run_job(job)
            except CaptureError as exc:
                self._ui(lambda e=exc: self.overlay.set_error(str(e)))
            except TranslateError as exc:
                self._ui(lambda e=exc: self.overlay.set_error(str(exc)))
            except Exception as exc:  # worker 绝不能静默死掉
                self._ui(lambda e=exc: self.overlay.set_error(f"内部错误：{e}"))
            finally:
                if job == "inline":
                    self._inline_busy = False

    def _run_inline_job(self) -> None:
        """「游戏里已经打了中文，就地翻掉」：截输入框 → 读出中文翻成外文 → 替换发送。

        和聊天栏翻译共用截图和模型，只是换了块区域、换了提示词：
        那边要的是「别人说了啥」，这边要的是一句能发出去的外文。
        """
        profile = self.cfg.profile()
        rect = profile.input_rect

        if rect is None:
            self._ui(lambda: self.window.set_status(
                "还没框选「聊天输入框」区域 —— 设置里的区域页框一下", "error"))
            return
        if self.translator is None:
            self._ui(lambda: self.window.set_status(
                "翻译后端没初始化成功，检查 API key", "error"))
            return

        self._hide_overlay_for_capture(rect)
        frame = self.capture.capture(rect)

        target = profile.reply_lang or DEFAULT_REPLY_LANG
        try:
            result = self.translator.translate_image_text(
                image_data=frame.data,
                media_type=frame.media_type,
                target=target,
                glossary=self.active_entries(),
            )
        except TranslateError as exc:
            self._ui(lambda e=exc: self.window.set_status(f"翻译失败：{e}", "error"))
            return
        except Exception as exc:
            self._ui(lambda e=exc: self.window.set_status(f"出错了：{exc}", "error"))
            return

        translated = (result.text or "").strip()
        if not translated:
            self._ui(lambda: self.window.set_status(
                "没读到输入框里的中文 —— 确认字已经打进去了、区域框对了没", "error"))
            return

        def apply() -> None:
            copied = self._copy_to_clipboard(translated)
            if profile.send_mode == "clipboard":
                self.window.set_status(
                    f"✔ 已复制：{translated}" if copied else "复制失败，剪贴板被占了",
                    "ok" if copied else "error",
                )
                return
            # 这条路不给「先看一眼」：游戏聊天框这会儿还开着，弹个小框抢焦点
            # 有可能把里面已经打的字弄丢。翻完就替换、发出去。
            self._send_to_game(profile.send_seq_inline, translated)

        self._ui(apply)

    def _run_job(self, job) -> None:
        if job == "inline":
            self._run_inline_job()
            return

        profile = self.cfg.profile()
        rect = profile.rect

        if rect is None:
            self._ui(lambda: self.overlay.set_error(
                "还没有框选区域。按 Ctrl+Alt+R 框选聊天栏。"))
            return

        if self.translator is None:
            self._ui(lambda: self.overlay.set_error(
                "翻译后端没初始化成功。检查 API key 和 config.json。"))
            return

        # 让悬浮窗知道该贴着哪块区域（side 模式靠它定位）
        self.overlay.set_anchor(rect)

        # 取消上一个在途请求 —— 连按热键时语义是"最新一次永远赢"
        if self._cancel is not None:
            self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel

        # ⚠️ 截图前必须先把悬浮窗收起来。
        # WDA_EXCLUDEFROMCAPTURE 对 mss 用的 GDI BitBlt 实测不生效
        # （API 返回 True，但截图照样拍得到）。悬浮窗只要压在聊天栏上，
        # 下一轮截图就会把上一轮的译文当成聊天内容再翻一遍，
        # 表现就是译文莫名其妙重复出现。
        self._hide_overlay_for_capture(rect)

        frame = self.capture.capture(rect)

        # 诊断：截到的是不是黑屏？独占全屏的游戏会这样 —— 热键收到了、
        # 也截图了，但拍出来一片黑，表现和"热键没反应"一模一样。
        if os.environ.get("GCT_HOTKEY_LOG"):
            try:
                from PIL import ImageStat

                mean = ImageStat.Stat(frame.image.convert("L")).mean[0]
                self._debug_log(
                    f"截图 {frame.width}x{frame.height} 平均亮度 {mean:.1f}"
                    + ("   <<< 疑似黑屏！" if mean < 8 else "")
                )
            except Exception:
                pass

        # ① 画面和上次几乎一样 —— 一次模型调用都不用发。
        # 半透明聊天框的游戏（背景一直在动）可以把这个关掉，见 config.py。
        if self.cfg.skip_unchanged and self._screen_unchanged(frame):
            self._stats["skipped"] += 1
            self._ui(lambda: self.overlay.set_result(
                "（没有新消息）", "画面没变 · 没花 token"))
            return

        # 设了 GCT_SAVE_CAPTURE=1 就把每次截图存到 captures/ 下，
        # 用来事后核对"模型到底看到了什么" —— 排查译文重复这类问题时是关键证据。
        if os.environ.get("GCT_SAVE_CAPTURE"):
            try:
                debug_dir = PROJECT_DIR / "captures"
                debug_dir.mkdir(exist_ok=True)
                stamp = time.strftime("%m%d_%H%M%S", time.localtime())
                (debug_dir / f"{stamp}_{frame.content_hash[:6]}.jpg").write_bytes(
                    frame.data
                )
            except OSError:
                pass

        entries = self.active_entries()

        key = self.cache.make_key(
            frame.content_hash,
            profile.prompt,
            profile.model,
            glossary=entries,
            translate_names=self.cfg.translate_names,
        )

        cached = self.cache.get(key)
        if cached is not None:
            self._stats["cache_hits"] += 1

            def _show_cached() -> None:
                self._present(frame, cached, "缓存命中 · 这轮没花 token")
                self._log_result(frame, cached, self.overlay.get_text())

            self._ui(_show_cached)
            return

        self._stats["requests"] += 1
        self._ui(lambda: self.overlay.begin_stream())

        req = TranslateRequest(
            image_data=frame.data,
            media_type=frame.media_type,
            prompt=profile.prompt,
            glossary=entries,
            translate_names=self.cfg.translate_names,
            model=profile.model,
            max_tokens=profile.max_tokens,
            temperature=profile.temperature,
            content_hash=frame.content_hash,
        )

        final = None
        for event in self.translator.translate_stream(req, cancel):
            if cancel.is_set():
                return
            if isinstance(event, Delta):
                # 故意不往悬浮窗里送。
                # 流式吐出来的是整块译文，其中旧消息那几行随后会被
                # "只显示新增"过滤掉 —— 先贴一遍再替换，视觉上就是
                # 旧对话白滚一轮。等 Done 拿到完整结果再一次性显示。
                pass
            elif isinstance(event, Failed):
                self._ui(lambda e=event.error: self.overlay.set_error(str(e)))
                return
            elif isinstance(event, Done):
                final = event.result

        if final is None:
            return

        text = final.text.strip()

        # 空结果要分情况说清楚，不能显示一片空白 ——
        # 否则用户看到的就是"按了热键但什么都没出来"。
        if not text:
            message = (
                "模型只输出了思考内容，没有给出译文"
                if final.reasoning_only
                else "画面里没有识别到文字"
            )

            def _show_empty(m=message) -> None:
                self.overlay.set_result(m, "空结果")
                self._log_result(frame, final, self.overlay.get_text())

            self._ui(_show_empty)
            return

        if text in ("<空>", "＜空＞", "<空/>"):
            self._ui(lambda ms=final.latency_ms: self.overlay.set_result(
                "这个区域里没有可翻译的文字", f"{ms} ms"))
            return

        if not final.incomplete:
            self.cache.put(key, final)

        meta = f"{final.latency_ms} ms"
        if final.incomplete:
            meta += " · 连接中断，结果可能不完整"

        def _show() -> None:
            self._present(frame, final, meta)
            self._log_result(frame, final, self.overlay.get_text())

        self._ui(_show)
        self._debug_log(f"翻译完成：{len(text)} 字")

    # ---------------------------------------------------------- 框选

    def _start_select(self, field: str = "rect") -> None:
        """框选区域。field 决定存到 Profile 的哪个字段 —— 聊天栏还是聊天输入框。"""
        if self.selector.is_open():
            return

        def done(rect):
            self.root.deiconify()  # 框选完把主窗口还回来

            if rect is None:
                self._refresh_ready_state()
                return

            setattr(self.cfg.profile(), field, rect)
            if field == "rect":
                self.overlay.set_anchor(rect)
                self.cfg.overlay.last_pos = None  # 换了区域，之前拖的位置不再合适
                self._last_lines = []  # 换了区域，上次的行记录作废
                self._last_image = None  # 同理，比对的参照也清掉
                self._last_items = []  # 换了地方看，上一批条目不算数了
            try:
                self.store.save(self.cfg)
            except OSError as exc:
                self.window.set_status(f"保存区域失败：{exc}", "error")
                return

            self.window.refresh_region()
            self._refresh_ready_state()

            if field == "input_rect":
                self.overlay.set_result(
                    f"输入框区域已保存：{rect.width} × {rect.height}\n\n"
                    "在游戏里打好中文，按 Ctrl+Alt+I 就地翻译并发出。",
                    "搞定",
                )
            else:
                self.overlay.set_result(
                    f"区域已保存：{rect.width} × {rect.height}\n"
                    f"位置 ({rect.left}, {rect.top})\n\n"
                    f"现在可以最小化窗口，按 "
                    f"{self.cfg.profile().hotkey} 翻译。",
                    "试试看",
                )

        self.overlay.hide()
        self.root.withdraw()
        try:
            self.selector.open(done)
        except Exception as exc:
            # 框选窗口没打开成功的话，主窗口已经藏起来了，
            # 不兜这一下用户会对着空屏幕，什么都点不到。
            self.root.deiconify()
            self.window.set_status(f"打开框选失败：{exc}", "error")
            return

        if not self.selector.is_open():
            # open() 没报错但窗口也没建起来，同样得把主窗口放回来
            self.root.deiconify()
            self.window.set_status("框选窗口没能打开，再试一次", "error")

    # ---------------------------------------------------------- 退出

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True

        if self._cancel is not None:
            self._cancel.set()

        self.hotkeys.stop()
        self.tray.stop()

        self.jobs.put(None)
        if self.worker is not None:
            self.worker.join(timeout=2)

        if self.translator is not None:
            self.translator.close()

        try:
            self.cfg.api_key = self.window.api_var.get().strip() or self.cfg.api_key
            self.store.save(self.cfg)
        except (OSError, tk.TclError) as exc:
            print(f"保存配置失败：{exc}", file=sys.stderr)

        print(f"统计：发起 {self._stats['requests']} 次请求，"
              f"缓存命中 {self._stats['cache_hits']} 次，"
              f"画面没变跳过 {self._stats['skipped']} 次")

        self.overlay.destroy()

        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass


def _acquire_single_instance():
    """单实例守卫。

    重复启动会带来一连串麻烦：多个实例互抢全局热键（后启动的那个注册失败），
    窗口叠在一起分不清哪个是哪个，前一个卡住时新的又用不了。
    用命名互斥体把这条路堵掉。返回 None 表示已经有实例在跑了。

    注意：返回值必须一直持有，句柄被回收掉互斥体就失效了。
    """
    kernel32 = winutil.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p
    ]

    handle = kernel32.CreateMutexW(
        None, False, "Global\\GameChatTranslator_SingleInstance"
    )
    ERROR_ALREADY_EXISTS = 183
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def _already_running_notice() -> None:
    try:
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "已经在运行了",
            "翻译器已经开着一个，不用重复启动。\n\n"
            "找不到它的窗口？按 Ctrl+Alt+H 唤回来。\n\n"
            "如果它完全没反应：先按 Ctrl+Alt+Q；还不行就打开任务管理器，"
            "结束里面的 pythonw.exe，再重新双击启动。",
        )
        root.destroy()
    except Exception:
        print("翻译器已经在运行了。")


def _emergency_cleanup(app) -> None:
    """启动过程中途失败时，把已经占住的资源放掉。

    热键是在 start() 前半段注册的，如果后面某行抛异常，
    进程虽然报了错但热键还占着 —— 用户再启动就会撞上
    "已经在运行"或者热键被占用，而且旧进程还卡在弹窗上不退。
    """
    if app is None:
        return
    for action in (
        lambda: app.hotkeys.stop(),
        lambda: app.overlay.destroy(),
        lambda: app.root.destroy(),
    ):
        try:
            action()
        except Exception:
            pass


def _install_crash_log():
    """把没接住的异常写进「错误日志.txt」，返回给 Tk 也挂一份。

    打包成 exe 是 --noconsole，没有控制台 —— 真闪退了用户只会说"打不开"，
    再问他什么也问不出来。留个文件至少能看出是哪一行炸的。
    """
    import traceback
    from datetime import datetime

    log_path = PROJECT_DIR / "错误日志.txt"
    shown = 0

    def record(exc_type, exc_value, exc_tb) -> None:
        nonlocal shown
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 58}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except OSError:
            pass

        # 弹窗限次：真进了异常循环的话，别让对话框把屏幕糊满
        if shown >= 3:
            return
        shown += 1
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "出错了", f"{exc_value}\n\n细节记在这个文件里：\n{log_path}"
            )
        except Exception:
            pass

    sys.excepthook = record
    return record


def main() -> int:
    # 中文 Windows 上被重定向时 stdout 会退回 GBK，中文日志会炸
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # ⚠️ 必须在创建任何 Tk 窗口之前
    winutil.set_dpi_awareness()

    crash_log = _install_crash_log()

    # 重复启动会互抢全局热键，还会让人分不清哪个窗口是哪个
    guard = _acquire_single_instance()
    if guard is None:
        _already_running_notice()
        return 1

    print("=" * 56)
    print("  游戏聊天栏翻译器")
    admin = winutil.is_admin()
    print(f"  权限：{'管理员' if admin else '普通用户'}"
          + ("" if admin else "   ← 游戏若以管理员运行，热键会对它失效"))
    print("=" * 56)

    app = None
    try:
        app = App()
        # Tk 回调里炸的异常不走 sys.excepthook，得单独挂一份，
        # 否则按钮点下去没反应、日志里一片空白
        app.root.report_callback_exception = crash_log
        app.start()
    except Exception as exc:
        # GUI 模式下出错用户什么都看不到，所以弹个框
        import traceback

        traceback.print_exc()

        # 关键：启动失败也得把已经占住的资源放掉。
        # 热键是在 start() 前半段注册的，如果后面某行抛异常，
        # 进程虽然报错了但热键还占着 —— 用户再启动就会看到
        # "已经在运行"或者热键冲突，而且进程还卡在错误弹窗上不退。
        _emergency_cleanup(app)

        try:
            from tkinter import messagebox

            messagebox.showerror(
                "启动失败",
                f"{type(exc).__name__}: {exc}\n\n"
                f"程序会退出。重新双击启动一次就行。",
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
