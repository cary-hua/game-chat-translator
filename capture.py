"""屏幕截图：抓取 → 缩放 → JPEG 编码 → 内容 hash。

只读屏幕，不碰任何游戏进程。
"""

import base64
import hashlib
import io
import threading
import time
from dataclasses import dataclass, field

import mss
from PIL import Image, ImageChops, ImageStat

from config import CaptureConfig, Rect


class CaptureError(Exception):
    """截图失败。常见原因：窗口拖到了独占全屏的游戏上。"""


@dataclass(frozen=True)
class Frame:
    """一帧截图及其元信息。"""

    data: bytes  # 编码后的图片字节
    media_type: str  # "image/jpeg"
    width: int
    height: int
    content_hash: str  # 基于原始像素，用于缓存键
    captured_at: float
    # 编码前（但已缩放）的 PIL 图，给"画面变没变"的比对用。
    # compare/repr 关掉：PIL 图参与相等比较没意义，打印出来更是一屏乱码。
    image: object = field(default=None, compare=False, repr=False)

    def to_data_url(self) -> str:
        b64 = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.media_type};base64,{b64}"

    def size_kb(self) -> float:
        return len(self.data) / 1024


# mss 实例内部持有 GDI DC，不能跨线程共享 —— 跨线程用会返回黑图甚至崩。
# 按线程各存一份。
_tls = threading.local()


def _sct():
    inst = getattr(_tls, "sct", None)
    if inst is None:
        # mss 10.x 里 mss.mss 已废弃，新入口是 mss.MSS
        factory = getattr(mss, "MSS", None) or mss.mss
        inst = factory()
        _tls.sct = inst
    return inst




class ScreenCapture:
    def __init__(self, cfg: CaptureConfig) -> None:
        self.cfg = cfg

    # ---------------------------------------------------------- 抓取

    def grab_image(self, rect: Rect) -> Image.Image:
        """抓取区域，返回 PIL Image（原始尺寸，未缩放）。"""
        if not rect.is_valid():
            raise CaptureError(f"区域太小: {rect.width}x{rect.height}")
        try:
            shot = _sct().grab(rect.as_mss_dict())
        except Exception as exc:  # mss 会抛自己的 ScreenShotError
            raise CaptureError(f"截图失败: {exc}") from exc

        # mss 给的是 BGRA 原始字节，用 BGRX 解出 RGB
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def capture(self, rect: Rect) -> Frame:
        """完整链路：抓取 → hash → 缩放 → 编码。"""
        img = self.grab_image(rect)

        # hash 必须在缩放之前、基于原始像素算。
        # 这样以后调整 JPEG 质量或 upscale 参数，已有缓存不会全部失效。
        content_hash = self.content_hash(img)

        img = self._prepare(img)
        data, media_type = self._encode(img)

        return Frame(
            data=data,
            media_type=media_type,
            width=img.width,
            height=img.height,
            content_hash=content_hash,
            captured_at=time.time(),
            image=img,
        )

    # ---------------------------------------------------------- 处理

    def _prepare(self, img: Image.Image) -> Image.Image:
        cfg = self.cfg

        if cfg.upscale and cfg.upscale != 1.0:
            w = max(1, round(img.width * cfg.upscale))
            h = max(1, round(img.height * cfg.upscale))
            img = img.resize((w, h), Image.LANCZOS)

        longest = max(img.width, img.height)
        if longest > cfg.max_edge:
            ratio = cfg.max_edge / longest
            img = img.resize(
                (max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                Image.LANCZOS,
            )
        return img

    def _encode(self, img: Image.Image) -> tuple[bytes, str]:
        buf = io.BytesIO()
        img.save(
            buf,
            format="JPEG",
            quality=self.cfg.jpeg_quality,
            optimize=True,
            # subsampling=0 即 4:4:4，不做色度抽样。
            # 默认的 4:2:0 会把小字号彩字糊成一团，对游戏聊天栏是致命的。
            subsampling=0,
        )
        return buf.getvalue(), "image/jpeg"

    # ---------------------------------------------------------- 工具

    @staticmethod
    def content_hash(img: Image.Image) -> str:
        h = hashlib.sha256()
        h.update(f"{img.width}x{img.height}".encode("ascii"))
        h.update(img.convert("RGB").tobytes())
        return h.hexdigest()


# 两张截图差异低到这个程度，就认为画面没变。
# 实测：没变化 = 0.000，有新消息 = 0.775~1.629，中间余量很大。
UNCHANGED_THRESHOLD = 0.3


def images_unchanged(a: Image.Image, b: Image.Image,
                     threshold: float = UNCHANGED_THRESHOLD) -> bool:
    """两张图是不是基本一样（用来跳过没必要的模型调用）。"""
    if a is None or b is None or a.size != b.size:
        return False
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    stat = ImageStat.Stat(diff)
    return (sum(stat.mean) / len(stat.mean)) < threshold


if __name__ == "__main__":
    # 手动验证：python capture.py [left top width height] [输出文件]
    import sys

    argv = sys.argv[1:]
    if len(argv) >= 4:
        rect = Rect(int(argv[0]), int(argv[1]), int(argv[2]), int(argv[3]))
    else:
        rect = Rect(0, 0, 400, 150)
        print(f"未指定区域，默认截左上角 {rect.width}x{rect.height}")

    out_path = argv[4] if len(argv) >= 5 else "debug_capture.jpg"

    cap = ScreenCapture(CaptureConfig())
    frame = cap.capture(rect)
    with open(out_path, "wb") as f:
        f.write(frame.data)

    print(f"区域     : {rect.left},{rect.top} {rect.width}x{rect.height}")
    print(f"已保存   : {out_path}")
    print(f"输出尺寸 : {frame.width}x{frame.height}")
    print(f"体积     : {frame.size_kb():.1f} KB")
    print(f"hash     : {frame.content_hash[:16]}")
