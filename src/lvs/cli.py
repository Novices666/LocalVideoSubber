"""LocalVideoSubber 命令行入口。

用法：
    lvs check                 环境自检
    lvs download --list       查看可下载模型
    lvs download --status     查看本地模型
    lvs test                  运行项目测试
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import _tag, load_config, run_startup_checks

ROOT = Path(__file__).resolve().parents[2]


def _force_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass


_force_utf8_stdio()


def _check(args: list[str]) -> int:
    config_path = None
    if args:
        if len(args) == 2 and args[0] == "--config":
            config_path = args[1]
        else:
            print("用法: lvs check [--config PATH]", file=sys.stderr)
            return 2

    cfg = load_config(config_path)
    items = run_startup_checks(cfg)
    print("\nLocalVideoSubber 环境自检\n")
    for item in items:
        kind = "必需" if item.level == "required" else "可选"
        print(f"[{_tag(item)}] {item.name} ({kind})\n  {item.detail}")
    blocked = [item for item in items if not item.ok and item.level == "required"]
    print("\n结论:", "环境就绪" if not blocked else "缺少必需组件")
    return 0 if not blocked else 1


def _help() -> None:
    print(__doc__)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        _help()
        return 0

    command, args = argv[0], argv[1:]
    if command == "check":
        return _check(args)
    if command in ("download", "dl", "models"):
        return subprocess.call(
            [sys.executable, str(ROOT / "scripts" / "download_models.py"), *args]
        )
    if command in ("test", "tests"):
        return subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)

    print(f"未知命令: {command}\n", file=sys.stderr)
    _help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
