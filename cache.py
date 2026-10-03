"""翻译结果缓存：LRU + 条数/字节双上限 + TTL。

游戏聊天里 "hi" "gg" "lol" 和刷屏的重复率极高，
按截图内容缓存能省掉一大半请求，体感也快很多。
"""

import hashlib
import threading
import time
from collections import OrderedDict


class TranslationCache:
    def __init__(
        self,
        max_entries: int = 200,
        max_bytes: int = 2_000_000,
        ttl: float = 600.0,
    ) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl = ttl

        self._data: OrderedDict[str, tuple] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

        self.hits = 0
        self.misses = 0

    # ---------------------------------------------------------- 键

    @staticmethod
    def make_key(
        content_hash: str,
        prompt: str,
        model: str,
        target_lang: str = "zh-CN",
        glossary: list | None = None,
        translate_names: bool = False,
    ) -> str:
        """改提示词 / 换模型 / 改术语表 / 改名字开关，键都会变，旧缓存自动失效。"""
        h = hashlib.sha256()
        h.update(content_hash.encode("utf-8"))
        h.update(b"\x00")
        h.update(prompt.encode("utf-8"))
        h.update(b"\x00")
        h.update(model.encode("utf-8"))
        h.update(b"\x00")
        h.update(target_lang.encode("utf-8"))
        h.update(b"\x00")
        h.update(str(translate_names).encode("utf-8"))
        if glossary:
            h.update(b"\x00")
            # 条目顺序可能变，排序后再算，保证同样的表得到同样的键
            for key in sorted(
                f"{e.get('term')}={e.get('translation')}={e.get('translate')}"
                for e in glossary if isinstance(e, dict)
            ):
                h.update(key.encode("utf-8") + b"\x00")
        return h.hexdigest()

    # ---------------------------------------------------------- 读写

    def get(self, key: str):
        """命中返回值并把它移到 LRU 队尾。惰性过期，不起后台线程。"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None

            value, expires_at, size = entry
            if time.time() > expires_at:
                del self._data[key]
                self._bytes -= size
                self.misses += 1
                return None

            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value) -> None:
        size = len(value.text.encode("utf-8")) + 256  # 256 是对象本身的粗略开销
        with self._lock:
            if key in self._data:
                _, _, old_size = self._data.pop(key)
                self._bytes -= old_size
            self._data[key] = (value, time.time() + self.ttl, size)
            self._bytes += size
            self._evict()

    def _evict(self) -> None:
        """条数和字节双上限。

        只限条数不够 —— 单条长译文可能几十 KB，200 条也能撑爆内存。
        """
        while self._data and (
            len(self._data) > self.max_entries or self._bytes > self.max_bytes
        ):
            _, (_, _, size) = self._data.popitem(last=False)
            self._bytes -= size

    # ---------------------------------------------------------- 其他

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "bytes": self._bytes,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }
