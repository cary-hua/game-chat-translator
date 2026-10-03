"""提交前扫一遍暂存区，确认没有不该出去的东西。

用法：
    git add .
    python 开源前检查.py      ← 先过这一关
    git commit -m "..."

只读 git 的暂存区（`git add` 之后、`commit` 之前那个状态），不改任何东西。
发现问题就列出来并以非零码退出，让你先处理。

**为什么非要这么一道**：`.gitignore` 只拦"还没被跟踪过的文件"。
一旦某个文件先被 `git add` 过，再往 `.gitignore` 里补规则**是没用的** ——
它已经在暂存区里躺着，commit 照样带走。所以别指望忽略规则能兜底，
每次提交前扫一眼才算数。
"""

import re
import subprocess
import sys
from pathlib import Path

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent

# 这些文件名/目录绝不该出现在提交里
FORBIDDEN_NAMES = {
    "config.json", "config.json.bak", "config.json.tmp",
    "history.jsonl", "错误日志.txt",
}
FORBIDDEN_PARTS = {
    "build", "dist", "发布", "__pycache__", "captures",
    ".venv", "venv", "_exe测试",
}
FORBIDDEN_SUFFIX = {".zip", ".spec", ".pyc", ".log"}

# 内容里出现这些就算踩雷。
#
# ⚠️ 末尾都要 {20,}：README 里写着 `set GCT_API_KEY=sk-你的key` 这种示例，
# 不卡长度的话那条示例就会被当成真 key 报出来（踩过一次）。
# 真 key 是 sk- 后面跟一长串字母数字，示例写的是中文占位符。
SENSITIVE_PATTERNS = [
    ("API key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("本机用户名", re.compile(r"mechrevo", re.I)),
    ("本机绝对路径", re.compile(r"[A-Za-z]:[\\/](?:项目|Users[\\/]mechrevo)")),
]

MAX_FILE_MB = 2.0  # 单个文件超过这个大小就提醒一下（assets 除外）


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"✘ git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def staged_files() -> list[str]:
    """暂存区里的文件（相对路径，正斜杠）。"""
    out = run_git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def main() -> int:
    try:
        files = staged_files()
    except SystemExit as exc:
        print(exc)
        return 1

    if not files:
        print("暂存区是空的 —— 先 git add .")
        return 1

    print(f"暂存区里有 {len(files)} 个文件，开始检查…\n")

    problems: list[str] = []
    warnings: list[str] = []

    # 本机 config.json 里的 key 也拿来当样本比对
    local_keys: list[str] = []
    local_cfg = ROOT / "config.json"
    if local_cfg.exists():
        import json

        try:
            key = str(json.loads(local_cfg.read_text(encoding="utf-8"))
                      .get("api_key", "")).strip()
            if key:
                local_keys.append(key)
        except (OSError, ValueError):
            pass

    for rel in files:
        path = ROOT / rel
        parts = set(Path(rel).parts)
        name = Path(rel).name

        # ① 文件名 / 目录黑名单
        if name in FORBIDDEN_NAMES:
            problems.append(f"{rel}  ← 这个文件名就不该提交")
            continue
        if parts & FORBIDDEN_PARTS:
            problems.append(f"{rel}  ← 在 {parts & FORBIDDEN_PARTS} 目录下，应被忽略")
            continue
        if Path(rel).suffix.lower() in FORBIDDEN_SUFFIX:
            problems.append(f"{rel}  ← 这个后缀不该提交")
            continue

        if not path.exists():
            continue

        # ② 体积
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > MAX_FILE_MB and not rel.startswith("assets/"):
            warnings.append(f"{rel}  {size_mb:.1f} MB —— 有点大，确认下要不要提交")

        # ③ 内容
        if not is_text(path):
            continue
        text = path.read_text(encoding="utf-8")

        for label, pattern in SENSITIVE_PATTERNS:
            hit = pattern.search(text)
            if hit:
                shown = hit.group(0)
                if len(shown) > 24:
                    shown = shown[:12] + "…"
                problems.append(f"{rel}  ← 里面有「{label}」：{shown}")

        for key in local_keys:
            if key in text:
                problems.append(f"{rel}  ← 里面有你的 API key！")

    # ④ 本地那些文件本来就在忽略列表里，顺便确认一下
    for guard in ("config.json", "history.jsonl"):
        if (ROOT / guard).exists() and guard not in files:
            pass  # 没被暂存，正确

    print("=" * 60)
    if problems:
        print(f"✘ 有 {len(problems)} 处必须处理：\n")
        for p in problems:
            print(f"   {p}")
        print()
        print("处理办法：")
        print("    git rm --cached <文件>      从暂存区拿掉（本地文件不动）")
        print("    把规则补进 .gitignore")
        print()
        if warnings:
            print(f"（另有 {len(warnings)} 处提醒）")
            for w in warnings:
                print(f"   · {w}")
        return 1

    print("✔ 暂存区干净，可以提交")
    if warnings:
        print()
        print(f"提醒（不拦你）：")
        for w in warnings:
            print(f"   · {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
