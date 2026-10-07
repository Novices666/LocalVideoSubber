#!/usr/bin/env python
"""模型下载器 —— 一键拉取 ASR / 翻译模型到 models/ 目录。

用法：
    python scripts/download_models.py --list                 # 看可选模型
    python scripts/download_models.py --asr large-v3-turbo   # 下载 ASR 模型
    python scripts/download_models.py --asr tiny --device cpu
    python scripts/download_models.py --gguf qwen3.5-9b-q4_k_m
    python scripts/download_models.py --all-basic            # tiny + 常用翻译模型

国内默认走 hf-mirror.com（可用 --endpoint 覆盖）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 cp936，输出箭头/图标会崩。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001
            pass


_force_utf8_stdio()

DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

# --------------------------------------------------------------------------
# ASR 模型目录（faster-whisper 的 CT2 转换版）
# --------------------------------------------------------------------------
ASR_MODELS = {
    "tiny":           ("Systran/faster-whisper-tiny",            "~75 MB",  "最快，质量低，适合测试"),
    "tiny.en":        ("Systran/faster-whisper-tiny.en",         "~75 MB",  "英文专用"),
    "base":           ("Systran/faster-whisper-base",            "~145 MB", "快速，质量一般"),
    "small":          ("Systran/faster-whisper-small",           "~480 MB", "速度质量平衡"),
    "medium":         ("Systran/faster-whisper-medium",          "~1.5 GB", "质量较好，8G 显存可跑"),
    "large-v3":       ("Systran/faster-whisper-large-v3",        "~3.1 GB", "最高质量，8G 显存 int8 可跑"),
    "large-v3-turbo": ("deepdml/faster-whisper-large-v3-turbo-ct2", "~1.6 GB", "推荐：接近 large 质量，快 4-8 倍"),
    "distil-large-v3":("Systran/faster-distil-whisper-large-v3",  "~1.5 GB", "英文蒸馏版，快"),
    "belle-zh":       ("BELLE-2/Belle-whisper-large-v3-zh",       "~3.1 GB", "中文优化版 large-v3"),
}

# --------------------------------------------------------------------------
# whisper.cpp GGML 模型（单文件）
# --------------------------------------------------------------------------
GGML_MODELS = {
    "ggml-tiny.bin":            ("ggerganov/whisper.cpp", "ggml-tiny.bin",            "~75 MB"),
    "ggml-base.bin":            ("ggerganov/whisper.cpp", "ggml-base.bin",            "~142 MB"),
    "ggml-small.bin":           ("ggerganov/whisper.cpp", "ggml-small.bin",           "~466 MB"),
    "ggml-medium.bin":          ("ggerganov/whisper.cpp", "ggml-medium.bin",          "~1.5 GB"),
    "ggml-large-v3-turbo.bin":  ("ggerganov/whisper.cpp", "ggml-large-v3-turbo.bin",  "~1.6 GB"),
    "ggml-large-v3.bin":        ("ggerganov/whisper.cpp", "ggml-large-v3.bin",        "~3.1 GB"),
}

# --------------------------------------------------------------------------
# 翻译模型（GGUF，给 llama.cpp / LM Studio 用）
# --------------------------------------------------------------------------
GGUF_MODELS = {
    "qwen3.5-9b-q4_k_m": (
        "lmstudio-community/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf",
        "~5.2 GB", "推荐：201 语言，结构化输出强，8G 显存合适",
    ),
    "qwen2.5-7b-instruct-q4_k_m": (
        "Qwen/Qwen2.5-7B-Instruct-GGUF", "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
        "~4.7 GB", "老型号，稳定，社区验证多",
    ),
    "qwen2.5-3b-instruct-q4_k_m": (
        "Qwen/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q4_k_m.gguf",
        "~1.9 GB", "小显存/纯 CPU 可用",
    ),
    "qwen3-8b-q4_k_m": (
        "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf",
        "~5.0 GB", "新一代，推理更强",
    ),
    "gemma-3-12b-it-q4": (
        "unsloth/gemma-3-12b-it-GGUF", "gemma-3-12b-it-Q4_K_M.gguf",
        "~7.3 GB", "多语言翻译质量优秀，12G+ 显存",
    ),
    "aya-expanse-8b-q4": (
        "bartowski/aya-expanse-8b-GGUF", "aya-expanse-8b-Q4_K_M.gguf",
        "~5.1 GB", "专为多语言设计",
    ),
}


# --------------------------------------------------------------------------
def setup_endpoint(endpoint: str) -> None:
    os.environ["HF_ENDPOINT"] = endpoint
    print(f"[i] HuggingFace 端点: {endpoint}")


def check_endpoint(endpoint: str) -> bool:
    import requests

    try:
        r = requests.head(endpoint, timeout=8, allow_redirects=True)
        return r.status_code < 400
    except Exception:  # noqa: BLE001
        return False


def pick_endpoint(preferred: str | None = None) -> str:
    candidates = [preferred] if preferred else [DEFAULT_ENDPOINT, "https://huggingface.co"]
    for ep in candidates:
        if ep and check_endpoint(ep):
            print(f"[就绪] 端点可达: {ep}")
            return ep
    print("[注意] 所有端点都不可达，仍尝试用 " + (candidates[0] or DEFAULT_ENDPOINT))
    return candidates[0] or DEFAULT_ENDPOINT


# --------------------------------------------------------------------------
def download_snapshot(
    repo: str,
    dest: Path,
    allow_patterns: list[str] | None = None,
    force: bool = False,
) -> Path:
    from huggingface_hub import snapshot_download

    dest.mkdir(parents=True, exist_ok=True)
    print(f"[下载] {repo}  ->  {dest}")
    path = snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        allow_patterns=allow_patterns,
        # 注意：whisper CT2 模型的词表文件名不固定（vocabulary.txt / .json / vocab.json），
        # 所以这里不能碰 *.txt / *.json，只排除文档和 git 元数据。
        ignore_patterns=["README.md", "*.md", ".gitattributes"],
        max_workers=4,
        force_download=force,
    )
    return Path(path)


def download_file(repo: str, filename: str, dest_dir: Path, force: bool = False) -> Path:
    from huggingface_hub import hf_hub_download

    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"[下载] {repo}/{filename}  ->  {dest_dir}")
    path = hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=str(dest_dir),
        force_download=force,
    )
    return Path(path)


# --------------------------------------------------------------------------
def install_asr(name: str, model_root: Path, endpoint: str, force: bool = False) -> int:
    if name not in ASR_MODELS:
        print(f"[错误] 未知 ASR 模型: {name}")
        print(f"    可选: {', '.join(ASR_MODELS)}")
        return 1
    repo, size, desc = ASR_MODELS[name]
    dest = model_root / "asr" / f"faster-whisper-{name}"
    print(f"\n=== {name}  ({size})  {desc} ===")
    try:
        download_snapshot(repo, dest, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 下载失败: {exc}")
        print("    提示：可手动从 https://hf-mirror.com/%s 下载后放到 %s" % (repo, dest))
        return 1
    print(f"[完成] {dest}")
    _verify_asr_model(dest, repo, force=force)
    print(f"    config.yaml 里填:  asr.model: asr/faster-whisper-{name}")
    return 0


# whisper CT2 模型必须齐备的文件。
# 词表文件名不固定：vocabulary.txt / vocabulary.json / vocab.json 都有实际在用，
# 所以这里用候选列表，满足任一个即可（与 faster-whisper 的 "vocabulary.*" 通配一致）。
_REQUIRED_ASR: tuple[str, ...] = ("config.json", "model.bin", "tokenizer.json")
_VOCAB_CANDIDATES: tuple[str, ...] = (
    "vocabulary.txt", "vocabulary.json", "vocab.json",
)


def _find_vocab(dest: Path) -> str | None:
    """返回实际存在的词表文件名。都不存在则返回 None。"""
    for name in _VOCAB_CANDIDATES:
        if (dest / name).exists():
            return name
    # 兜底：目录里任何以 vocab 开头的文件都算
    for p in sorted(dest.glob("vocab*")):
        if p.is_file():
            return p.name
    return None


def _verify_asr_model(dest: Path, repo: str, force: bool = False) -> bool:
    """校验关键文件是否下齐，缺则补拉。同时把缺的词表从远端仓库补齐。"""
    missing = [f for f in _REQUIRED_ASR if not (dest / f).exists()]

    # 词表单独处理：名字不固定，先看本地有没有，没有再去远端问
    if _find_vocab(dest) is None:
        missing.append("vocabulary.*")

    if not missing:
        return True

    print(f"[注意] 缺少文件，尝试补拉: {', '.join(missing)}")

    # 固定名文件直接拉
    for f in [m for m in missing if m != "vocabulary.*"]:
        try:
            download_file(repo, f, dest, force=force)
        except Exception as exc:  # noqa: BLE001
            print(f"[错误] 补拉 {f} 失败: {exc}")

    # 词表：先从远端问实际文件名
    if "vocabulary.*" in missing:
        for name in _list_vocab_files(repo) or _VOCAB_CANDIDATES:
            try:
                download_file(repo, name, dest, force=force)
                if _find_vocab(dest):
                    break
            except Exception:  # noqa: BLE001
                continue

    still = [f for f in _REQUIRED_ASR if not (dest / f).exists()]
    if _find_vocab(dest) is None:
        still.append("词表文件(vocabulary.txt/.json 或 vocab.json)")
    if still:
        print(f"[错误] 模型不完整，仍缺: {', '.join(still)}")
        print(f"    请手动从 https://hf-mirror.com/{repo}/tree/main 下载补到 {dest}")
        return False
    print("[完成] 文件已补全")
    return True


def _list_vocab_files(repo: str) -> list[str]:
    """向远端仓库查询实际存在的词表文件名。"""
    try:
        from huggingface_hub import list_repo_files

        files = list_repo_files(repo_id=repo)
        return [f for f in files if f.startswith("vocab") and "/" not in f]
    except Exception:  # noqa: BLE001
        return []


def install_ggml(name: str, model_root: Path, endpoint: str, force: bool = False) -> int:
    key = name if name.startswith("ggml-") else f"ggml-{name}.bin"
    if key not in GGML_MODELS:
        print(f"[错误] 未知 GGML 模型: {key}")
        print(f"    可选: {', '.join(GGML_MODELS)}")
        return 1
    repo, filename, size = GGML_MODELS[key]
    dest = model_root / "asr"
    print(f"\n=== {key}  ({size}) ===")
    try:
        download_file(repo, filename, dest, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 下载失败: {exc}")
        return 1
    print(f"[完成] {dest / filename}")
    print("    config.yaml 里填:  asr.engine: whispercpp")
    return 0


def install_gguf(name: str, model_root: Path, endpoint: str, force: bool = False) -> int:
    if name not in GGUF_MODELS:
        print(f"[错误] 未知翻译模型: {name}")
        print(f"    可选: {', '.join(GGUF_MODELS)}")
        return 1
    repo, filename, size, desc = GGUF_MODELS[name]
    dest = model_root / "translate" / name
    print(f"\n=== {name}  ({size})  {desc} ===")
    try:
        download_file(repo, filename, dest, force=force)
        # 分片模型：把同前缀的其他分片也拉下来
        if "-00001-of-" in filename:
            total = int(filename.split("-of-")[1].split(".")[0])
            prefix = filename.split("-00001-of-")[0]
            for i in range(2, total + 1):
                part = f"{prefix}-{i:05d}-of-{total:05d}.gguf"
                try:
                    download_file(repo, part, dest, force=force)
                except Exception as exc:  # noqa: BLE001
                    print(f"[注意] 分片 {part} 下载失败: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 下载失败: {exc}")
        return 1
    print(f"[完成] {dest}")
    print("    用 llama.cpp 起服务：")
    main_gguf = next(dest.glob("*.gguf"), dest / filename)
    print(f'      llama-server -m "{main_gguf}" -c 8192 -ngl 99 --port 8080')
    return 0


# --------------------------------------------------------------------------
def list_all(model_root: Path) -> None:
    print("\n可用 ASR 模型 (faster-whisper):")
    for k, (repo, size, desc) in ASR_MODELS.items():
        print(f"  {k:<18} {size:<9} {desc}")
    print("\n可用 ASR 模型 (whisper.cpp GGML):")
    for k, (repo, fn, size) in GGML_MODELS.items():
        print(f"  {k:<26} {size:<9}")
    print("\n可用翻译模型 (GGUF, 配 llama.cpp / LM Studio):")
    for k, (repo, fn, size, desc) in GGUF_MODELS.items():
        print(f"  {k:<30} {size:<9} {desc}")
    print(f"\n模型根目录: {model_root}")
    print("\n示例:")
    print("  python scripts/download_models.py --asr large-v3-turbo")
    print("  python scripts/download_models.py --gguf qwen3.5-9b-q4_k_m")
    print("  python scripts/download_models.py --all-basic")


def show_status(model_root: Path) -> None:
    print(f"\n模型目录: {model_root}")
    for sub in ("asr", "translate"):
        d = model_root / sub
        print(f"\n[{sub}]")
        if not d.is_dir() or not any(d.iterdir()):
            print("  (空)")
            continue
        for child in sorted(d.iterdir()):
            size = _human(_dir_size(child))
            print(f"  {child.name:<42} {size}")


def _dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="LocalVideoSubber 模型下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--asr", action="append", default=[], metavar="NAME",
                    help="下载 faster-whisper 模型 (可重复)")
    ap.add_argument("--ggml", action="append", default=[], metavar="NAME",
                    help="下载 whisper.cpp 模型 (可重复)")
    ap.add_argument("--gguf", action="append", default=[], metavar="NAME",
                    help="下载翻译用 GGUF 模型 (可重复)")
    ap.add_argument("--all-basic", action="store_true",
                    help="下载 tiny + large-v3-turbo + qwen3.5-9b")
    ap.add_argument("--list", action="store_true", help="列出可选模型")
    ap.add_argument("--status", action="store_true", help="显示已下载模型")
    ap.add_argument("--endpoint", default=None, help="HF 镜像地址")
    ap.add_argument("--model-root", default=None, help="模型根目录")
    ap.add_argument("--force", action="store_true", help="强制重新拉取/补齐已有模型")
    args = ap.parse_args()

    model_root = Path(args.model_root).resolve() if args.model_root else ROOT / "models"

    if args.list:
        list_all(model_root)
        return 0
    if args.status:
        show_status(model_root)
        return 0

    asr = list(args.asr)
    ggml = list(args.ggml)
    gguf = list(args.gguf)
    if args.all_basic:
        asr = list(dict.fromkeys(asr + ["tiny", "large-v3-turbo"]))
        gguf = list(dict.fromkeys(gguf + ["qwen3.5-9b-q4_k_m"]))
    if not (asr or ggml or gguf):
        ap.print_help()
        print("\n[注意] 没指定要下载什么。先跑 --list 看选项。")
        return 2

    endpoint = pick_endpoint(args.endpoint)
    setup_endpoint(endpoint)

    rc = 0
    for name in asr:
        rc |= install_asr(name, model_root, endpoint, force=args.force)
    for name in ggml:
        rc |= install_ggml(name, model_root, endpoint, force=args.force)
    for name in gguf:
        rc |= install_gguf(name, model_root, endpoint, force=args.force)

    print()
    show_status(model_root)
    if rc == 0:
        print("\n[完成] 全部模型下载完成。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
