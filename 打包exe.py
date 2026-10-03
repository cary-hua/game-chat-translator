"""把项目打成一个能直接发给别人的 exe。

用法：
    python 打包exe.py

这个脚本真正要保证的只有三件事：

  1. **config.json 绝不进包** —— 那里面有你的 API key，包出去等于送人。
     打完会在 exe 的二进制里搜一遍 key，搜到就报错、不出成品。
  2. **图标带上** —— assets 是随包资源，打包后程序从解压目录里找它，
     不是从 exe 旁边找，所以要显式 --add-data。
  3. **署名写上** —— 主界面底部、exe 属性、使用说明里都有。

产物在 发布/ 目录里，直接整个发出去就行。
"""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "发布"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"

APP_NAME = "游戏聊天栏翻译器"
VERSION = "1.0.0"
CREDIT = "如月见（YLR）"

# 用不到却容易被 PyInstaller 顺进来的大块头，排掉能小几 MB、启动也快点
EXCLUDES = [
    "pynput", "numpy", "scipy", "pandas", "matplotlib",
    "pytest", "IPython", "notebook", "PIL.ImageQt",
]

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', '{credit}'),
        StringStruct('FileDescription', '{name}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'game-translator'),
        StringStruct('LegalCopyright', '{credit}　仅供学习交流，请勿用于商业用途'),
        StringStruct('OriginalFilename', '{name}.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""

README_TEXT = """{name} v{version}
{credit} 制作　·　仅供学习交流，请勿用于商业用途
================================================

【它是干什么的】

外服游戏里看不懂队友说什么 —— 框住聊天栏按个热键，外文就翻成中文。
反过来你打中文，也能翻成日语/英语发出去。

翻译用的是 DeepSeek 的视觉模型，直接看截图出译文，不需要装 OCR。


【怎么开始】

1. 双击 {name}.exe
2. 第一次会让你填 API key —— 去 https://platform.deepseek.com
   注册一个，在「API Keys」里建一个，形如 sk-xxxxxxxx
   （要花钱，但很便宜，一条消息几分钱）

   也可以不填在文件里，改成设环境变量 GCT_API_KEY。

   想用别家 AI 也行：「API Key」页底下有「接口地址」和「模型名」两栏。
   前提是对方提供 OpenAI 兼容接口、而且模型能看图（本工具靠模型直接
   读截图，没有 OCR 那一层）。不会填就留默认的 DeepSeek，别猜。

3. 填完会让你框选聊天栏：把游戏切成「窗口化」或「无边框窗口化」，
   进游戏，框住聊天栏那一块。

   独占全屏（Exclusive Fullscreen）下截图是黑的、也没法显示悬浮窗，
   这个绕不过去，务必先改窗口模式。

4. 之后按 Ctrl+Alt+T 就能翻译了。窗口可以最小化到后台，不影响玩游戏。


【热键】

  Ctrl+Alt+T   翻译聊天栏（看别人说啥）
  Ctrl+Alt+E   弹出小框打中文，翻好自动发进游戏
  Ctrl+Alt+I   游戏里打了中文，就地翻掉替换
  Ctrl+Alt+R   重新框选区域
  Ctrl+Alt+H   显示 / 隐藏主窗口
  Ctrl+Alt+Q   退出

六个都能改：主界面「热键」页每一行末尾点「修改」。


【给日服玩家的两条】

· 「回话」那一页顶上把语言选成「日文」，默认就是。
· 术语表会自动倒过来用 —— 你写「辛苦了」，发出去是 おつ。
  想加自己的说法，去「术语表」页加。


【常见问题】

Q: 按热键没反应？
A: 有的游戏会屏蔽系统热键。程序默认已经用了兼容性最好的「轮询」；
   还不行就到「热键」页换成「键盘钩子」。

Q: 按了 Ctrl+Alt+E，游戏里却乱按一通？
A: 程序得知道你的游戏用哪个键开聊天框。到「回话」页「发送按键」那儿，
   把「开聊天框」改成你游戏用的键（大多是回车，有的用 T、Y、U）。

   拿不准就把「翻完怎么办」选成「只放剪贴板」—— 那档程序完全不碰
   你的键盘，译文进剪贴板你自己粘。

Q: 不想让它自动往游戏里发东西？
A: 同一页顶上「翻完怎么办」三档，选「只放剪贴板」或「先摆出来给我
   看一眼」，都行。

Q: 不想按热键，想让它自己翻？
A: 「聊天区域」页有「监视模式」，打开后它隔一会儿自己看一眼聊天栏，
   有新消息就翻出来。但这条只对「聊天框不透明、底下画面不动」的游戏
   有效 —— LOL 那种半透明聊天框不行，会一直误报、白花 token。
   拿不准就开着看几秒：要是它隔一会儿冒一次「（没有新消息）」，
   说明在误报，关掉。

Q: 译文发不进游戏？
A: 这游戏多半禁用了外部粘贴。程序发的是按键不是粘贴，正常能用；
   真发不进去就按 Ctrl+Alt+I 那条路，或者看下面的「反馈」。

Q: 悬浮窗盖不住游戏 / 截图全黑？
A: 游戏还在独占全屏。改成窗口化或无边框窗口化。

Q: 杀毒软件报警？
A: PyInstaller 打包的 exe 常被误报，加个信任即可。程序只截屏、
   只连 DeepSeek，不读游戏内存、不改游戏文件。

Q: 提示"已经在运行"？
A: 任务栏右下角找找托盘图标，可能已经开着了。


【出问题了怎么反馈】

程序目录下会生成「错误日志.txt」，把里面的内容发过来就能定位。
（没出错就不会有这个文件。）

配置和记录都放在 exe 同一个文件夹里，整个文件夹拷走就能搬机。
"""


def _say(text: str = "") -> None:
    print(text)


def make_icon() -> Path:
    """从 logo.png 生成 exe 用的 .ico。

    原图是 216x97 的横条，直接当图标会被拉变形 —— 补成正方形。
    而且图形是纯黑的，垫个白底，深色任务栏上才看得见。
    """
    from PIL import Image

    src = ASSETS / "logo.png"
    if not src.exists():
        raise SystemExit(f"✘ 找不到图标源文件：{src}")

    img = Image.open(src).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)

    ico = BUILD / "app.ico"
    BUILD.mkdir(exist_ok=True)
    canvas.save(
        ico, format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return ico


def make_version_file() -> Path:
    major, minor, patch = (list(map(int, VERSION.split("."))) + [0, 0, 0])[:3]
    text = VERSION_TEMPLATE.format(
        name=APP_NAME, version=VERSION, credit=CREDIT,
        major=major, minor=minor, patch=patch,
    )
    path = BUILD / "version_info.txt"
    BUILD.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def find_local_keys() -> list[str]:
    """本机上有哪些 API key —— 待会儿要在 exe 里搜它们。"""
    keys = []
    cfg = ROOT / "config.json"
    if cfg.exists():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            key = str(raw.get("api_key") or "").strip()
            if key:
                keys.append(key)
        except (OSError, ValueError):
            pass
    env_key = (os.environ.get("GCT_API_KEY") or "").strip()
    if env_key:
        keys.append(env_key)
    return keys


def verify_no_secrets(exe: Path, keys: list[str]) -> None:
    """在打出来的 exe 里搜 API key。

    「config.json 不在打包列表里」和「真没进去」是两回事 ——
    二进制里搜一遍才算数。
    """
    if not keys:
        _say("  · 本机没有 API key 可对照，跳过这项检查")
        return

    data = exe.read_bytes()
    for key in keys:
        if key.encode("utf-8") in data:
            raise SystemExit(
                f"\n✘✘ exe 里搜到了 API key（{key[:10]}…）—— 不能发出去！\n"
                f"   这个包已经作废，检查一下是不是把 config.json 加进了打包列表。"
            )
    _say(f"  ✔ exe 里没有 API key（比对了 {len(keys)} 个）")


def build() -> Path:
    if shutil.which("pyinstaller") is None and not _has_pyinstaller():
        raise SystemExit("✘ 没装 PyInstaller。先跑：pip install pyinstaller")

    _say("① 生成图标…")
    ico = make_icon()
    _say(f"   {ico}")

    _say("② 生成版本信息（含署名）…")
    version_file = make_version_file()

    _say("③ 调 PyInstaller（第一次会比较慢，几分钟）…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--name", APP_NAME,
        "--icon", str(ico),
        "--add-data", f"{ASSETS};assets",
        "--version-file", str(version_file),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        "--noconfirm",
    ]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "main.py"))

    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit("✘ 打包失败，看上面的报错。")

    exe = DIST / f"{APP_NAME}.exe"
    if not exe.exists():
        raise SystemExit(f"✘ 打包跑完了但没找到 {exe}")
    return exe


def _has_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


def check_dist_clean() -> None:
    """发布目录里只该有 exe 和使用说明。

    尤其防 config.json —— 那里面是 API key。它不会被打进 zip（zip 是逐个
    列文件的），但留在目录里容易让人以为"是不是要被发出去了"，万一以后
    谁改成整目录打包就真出事了。所以看到就提醒。
    """
    expected = {f"{APP_NAME}.exe", "使用说明.txt"}
    extras = sorted(p.name for p in DIST.iterdir() if p.name not in expected)
    if extras:
        _say(f"  ⚠ 发布目录里多了这些（没打进 zip，但建议清掉）：{'、'.join(extras)}")
    else:
        _say("  ✔ 发布目录干净（只有 exe 和说明）")


def main() -> int:
    _say("=" * 60)
    _say(f"  打包 {APP_NAME} v{VERSION}　·　{CREDIT}")
    _say("=" * 60)
    _say()

    keys = find_local_keys()
    exe = build()

    _say()
    _say("④ 验包…")
    verify_no_secrets(exe, keys)
    check_dist_clean()

    _say("⑤ 写使用说明…")
    readme = DIST / "使用说明.txt"
    readme.write_text(
        README_TEXT.format(name=APP_NAME, version=VERSION, credit=CREDIT),
        encoding="utf-8",
    )

    _say("⑥ 压缩成发布包…")
    zip_path = ROOT / f"{APP_NAME}_v{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, exe.name)
        z.write(readme, readme.name)

    size_mb = exe.stat().st_size / 1024 / 1024
    zip_mb = zip_path.stat().st_size / 1024 / 1024
    _say()
    _say("=" * 60)
    _say(f"  ✔ 完成")
    _say(f"    {zip_path}  ({zip_mb:.1f} MB)  ← 发这个")
    _say(f"    发布/ 文件夹里是解开的：exe {size_mb:.1f} MB + {readme.name}")
    _say()
    _say("  对方拿到 zip 解压，双击 exe，第一次自己填 API key。")
    _say("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
