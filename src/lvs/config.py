"""配置加载 / 模型路径解析 / 环境自检。

模型路径三级回退：
    1) 调用方显式给的绝对路径
    2) config.yaml 里配置的路径（相对 model_root 或绝对）
    3) model_root/<type>/<name> 默认目录
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 关键：在任何 import ctranslate2 之前预加载 CUDA DLL（见 cuda_dll.py 说明）。
# config.py 是几乎所有入口第一个 import 的 lvs 模块，这里预加载可保证
# check_cuda / faster_whisper 里的 import ctranslate2 都命中已加载的 DLL。
from .cuda_dll import preload_cuda_dlls

preload_cuda_dlls()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.yaml"

# 国内可达的 HF 镜像（huggingface.co 常被墙）
HF_MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]


def setup_hf_mirror(cfg: "Config | None" = None) -> str:
    """设置 HF_ENDPOINT 环境变量。已显式设置则不覆盖。"""
    if os.environ.get("HF_ENDPOINT"):
        return os.environ["HF_ENDPOINT"]
    prefer = (cfg.get("asr.hf_endpoint") if cfg else None) or HF_MIRRORS[0]
    os.environ["HF_ENDPOINT"] = prefer
    # 让 transformers / hf_hub 也走镜像
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    return prefer

# 各模型的默认子目录
MODEL_SUBDIRS = {
    "asr": "asr",
    "whispercpp": "asr",
    "translate": "translate",
}

# whisper.cpp 常见的量化后缀
GGML_SUFFIXES = (".bin", ".gguf")


class ConfigError(Exception):
    """配置相关错误。"""


# --------------------------------------------------------------------------
# 配置对象
# --------------------------------------------------------------------------
@dataclass
class Config:
    """扁平化包装的配置树，支持 cfg.get("asr.beam_size", 5) 点号取值。"""

    raw: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    # -- 点号路径访问 ------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, dotted: str, value: Any) -> None:
        keys = dotted.split(".")
        node = self.raw
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    def section(self, name: str) -> dict[str, Any]:
        val = self.raw.get(name)
        return copy.deepcopy(val) if isinstance(val, dict) else {}

    # -- 模型根目录 --------------------------------------------------------
    @property
    def model_root(self) -> Path:
        raw = self.get("model_root", "./models")
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def output_dir(self) -> Path:
        raw = self.get("jobs.output_dir", "./workspace")
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    def ensure_dirs(self) -> None:
        self.model_root.mkdir(parents=True, exist_ok=True)
        for sub in set(MODEL_SUBDIRS.values()):
            (self.model_root / sub).mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# 加载 / 保存
# --------------------------------------------------------------------------
def load_config(path: str | Path | None = None) -> Config:
    """加载配置。不存在时从 config.example.yaml 复制一份。"""
    cfg_path = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH

    if not cfg_path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(EXAMPLE_CONFIG_PATH, cfg_path)
        else:
            cfg_path.write_text("model_root: ./models\n", encoding="utf-8")

    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml 解析失败: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config.yaml 顶层必须是映射（key: value）")

    cfg = Config(raw=data, path=cfg_path)
    cfg.ensure_dirs()
    return cfg


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    """回写配置。"""
    target = Path(path).expanduser().resolve() if path else (cfg.path or DEFAULT_CONFIG_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(cfg.raw, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    cfg.path = target
    return target


def merge_overrides(cfg: Config, overrides: dict[str, Any]) -> Config:
    """返回叠加调用参数后的新 Config（不污染原对象）。"""
    new_raw = copy.deepcopy(cfg.raw)
    for dotted, value in overrides.items():
        if value is None:
            continue
        keys = dotted.split(".")
        node = new_raw
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return Config(raw=new_raw, path=cfg.path)


# --------------------------------------------------------------------------
# 模型路径解析（核心：三级回退）
# --------------------------------------------------------------------------
def resolve_model_path(
    cfg: Config,
    kind: str,
    explicit: str | None = None,
) -> tuple[str, str]:
    """解析模型位置。

    返回 (resolved, source)，其中 source ∈ {"explicit", "config", "default", "local-fallback", "repo"}。
    显式值优先于配置值；两者都先做精确匹配，失败后按名称模糊匹配本地模型。
    """
    sub = MODEL_SUBDIRS.get(kind, kind)

    def _exact_match(value: str) -> str | None:
        """精确匹配：绝对路径 / 相对 model_root / 相对项目根。"""
        p = Path(value).expanduser()
        if p.is_absolute():
            return str(p) if p.exists() else None
        rel = (cfg.model_root / value).resolve()
        if rel.exists():
            return str(rel)
        proj = (PROJECT_ROOT / value).resolve()
        if proj.exists():
            return str(proj)
        return None

    def _fuzzy_local(value: str) -> tuple[str | None, str]:
        """按名称模糊匹配 model_root/<sub>/ 下的本地完整模型。"""
        default_dir = cfg.model_root / sub
        if not default_dir.is_dir():
            return None, ""
        children = sorted(d for d in default_dir.iterdir() if d.is_dir())
        local_models = [d for d in children if _is_complete_faster_whisper(d)]
        if not local_models:
            return None, ""

        wanted = _model_name_key(value)

        def _score(path: Path) -> tuple[int, int]:
            key = _model_name_key(path.name)
            if wanted and (wanted in key or key in wanted):
                return (100 + min(len(key), len(wanted)), _dir_size_mb(path))
            return (0, _dir_size_mb(path))

        best = max(local_models, key=_score)
        if len(local_models) == 1 or _score(best)[0] > 0:
            return str(best), "local-fallback"
        return None, ""

    # 1) 显式值：先精确，后模糊
    if explicit and explicit.strip() and explicit.strip().lower() not in {"auto", "none"}:
        ex = explicit.strip()
        r = _exact_match(ex)
        if r:
            return r, "explicit"
        if kind == "asr":
            r, src = _fuzzy_local(ex)
            if r:
                return r, src

    # 2) 配置值：先精确，后模糊
    conf_val = cfg.get(f"{kind}.model")
    if isinstance(conf_val, str) and conf_val.strip():
        cv = conf_val.strip()
        r = _exact_match(cv)
        if r:
            return r, "config"
        if kind == "asr":
            r, src = _fuzzy_local(cv)
            if r:
                return r, src

    # 3) 默认目录扫描（唯一子目录 / 单文件）
    default_dir = cfg.model_root / sub
    if default_dir.is_dir():
        children = sorted(d for d in default_dir.iterdir() if d.is_dir())
        if len(children) == 1:
            return str(children[0]), "default"
        files = [f for f in default_dir.iterdir() if f.suffix.lower() in GGML_SUFFIXES]
        if len(files) == 1:
            return str(files[0]), "default"

    # 4) 兜底：交给 faster-whisper 按仓库名下载
    repo = conf_val if isinstance(conf_val, str) and conf_val.strip() else "large-v3"
    if "/" in repo and not Path(repo).is_absolute():
        repo = repo.split("/")[-1]  # "asr/xxx" -> "xxx"
    return repo, "repo"


def _model_name_key(value: Any) -> str:
    """Normalize a model path/name for fuzzy local-model matching."""
    if not isinstance(value, str):
        return ""
    name = Path(value.replace("\\", "/")).name.lower()
    for token in ("faster-whisper-", "faster_whisper_", "fasterwhisper"):
        name = name.replace(token, "")
    return name.replace("-", "").replace("_", "").replace(".", "")


def _is_complete_faster_whisper(path: Path) -> bool:
    """Return whether a local faster-whisper directory has its core files."""
    required = ("config.json", "model.bin", "tokenizer.json")
    if not path.is_dir() or any(not (path / name).is_file() for name in required):
        return False
    return any((path / name).is_file() for name in ("vocabulary.txt", "vocabulary.json", "vocab.json")) \
        or any(p.is_file() for p in path.glob("vocab*"))


def scan_models(cfg: Config, kind: str) -> list[dict[str, str]]:
    """扫描 model_root 下某类可用模型。

    faster-whisper 模型的 value 返回短名（如 large-v3-turbo），显示更友好；
    提交时后端 resolve_model_path 会自动模糊匹配到完整路径。
    """
    sub = MODEL_SUBDIRS.get(kind, kind)
    base = cfg.model_root / sub
    found: list[dict[str, str]] = []
    if not base.is_dir():
        return found

    def _short_name(name: str) -> str:
        for prefix in ("faster-whisper-", "faster_whisper_", "fasterwhisper"):
            if name.startswith(prefix):
                return name[len(prefix):]
        return name

    def _label(d: Path) -> tuple[str, str]:
        rel = d.relative_to(cfg.model_root).as_posix()
        size = _dir_size_mb(d) if d.is_dir() else d.stat().st_size / 1024 / 1024
        # ASR 目录用短名作为 value（large-v3-turbo），label 保留完整信息
        if kind == "asr" and d.is_dir():
            val = _short_name(d.name)
            return val, f"{val}  ({size:.0f} MB)"
        return rel, f"{rel}  ({size:.0f} MB)"

    for child in sorted(base.iterdir()):
        if child.is_dir():
            # 目录里得有模型文件才算数
            if any(child.rglob("*model*")) or any(
                f.suffix.lower() in (".bin", ".gguf") for f in child.rglob("*")
            ):
                val, label = _label(child)
                found.append({"value": val, "label": label})
        elif child.suffix.lower() in GGML_SUFFIXES:
            val, label = _label(child)
            found.append({"value": val, "label": label})

    # 追加默认目录直属的情况
    if not found and any(base.iterdir()):
        found.append({"value": sub, "label": f"{sub}  (默认目录)"})
    return found


def _dir_size_mb(d: Path) -> float:
    total = 0
    try:
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total / 1024 / 1024


# --------------------------------------------------------------------------
# 环境自检（启动确认门用）
# --------------------------------------------------------------------------
@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str
    level: str = "required"  # required | optional


def check_ffmpeg() -> CheckItem:
    exe = shutil.which("ffmpeg")
    if not exe:
        return CheckItem("FFmpeg", False, "未在 PATH 中找到，抽音频/渲染不可用", "required")
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=10
        ).stdout.splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        return CheckItem("FFmpeg", False, f"执行失败: {exc}", "required")
    return CheckItem("FFmpeg", True, out.strip(), "required")


def check_ffprobe() -> CheckItem:
    exe = shutil.which("ffprobe")
    if not exe:
        # 允许同目录下的 ffprobe
        ff = shutil.which("ffmpeg")
        if ff:
            cand = Path(ff).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if cand.exists():
                return CheckItem("FFprobe", True, str(cand), "required")
        return CheckItem("FFprobe", False, "未找到，媒体探测不可用", "required")
    return CheckItem("FFprobe", True, exe, "required")


def check_cuda() -> CheckItem:
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # noqa: BLE001
        return CheckItem("CUDA (CTranslate2)", False, f"检测异常: {exc}", "optional")
    if n > 0:
        name = "GPU"
        cap = ""
        try:
            import subprocess as sp

            r = sp.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().splitlines()[0].split(",")
                name = ", ".join(p.strip() for p in parts[:2])
                cap = parts[2].strip() if len(parts) > 2 else ""
        except Exception:  # noqa: BLE001
            pass

        # Blackwell (sm_120，RTX 50 系) 上 INT8 不可用，必须 float16
        is_blackwell = cap and float(cap) >= 12.0
        if is_blackwell:
            detail = f"{name} · Blackwell 架构，INT8 不可用，请用 float16 计算精度"
            return CheckItem("CUDA (CTranslate2)", _cublas_available(), detail, "optional")
        detail = f"{n} 个设备 · {name}"
        return CheckItem("CUDA (CTranslate2)", _cublas_available(), detail, "optional")
    return CheckItem(
        "CUDA (CTranslate2)", False, "未检测到 GPU，将回退 CPU（速度较慢）", "optional"
    )


def _cublas_available() -> bool:
    """检测 cuBLAS 运行库是否可用（nvidia-cublas-cu12 包的 DLL）。"""
    try:
        import glob
        import sysconfig

        site = sysconfig.get_paths()["purelib"]
        return len(glob.glob(os.path.join(site, "nvidia", "cublas", "bin", "cublas64_*.dll"))) > 0
    except Exception:  # noqa: BLE001
        return False


def check_faster_whisper() -> CheckItem:
    try:
        import faster_whisper  # noqa: F401

        return CheckItem("faster-whisper", True, "已安装", "required")
    except Exception as exc:  # noqa: BLE001
        return CheckItem("faster-whisper", False, f"未安装: {exc}", "required")


def _tag(item: CheckItem) -> str:
    """文本状态标签（取代彩色圆点）。"""
    if item.ok:
        return "通过"
    return "缺失" if item.level == "required" else "未就绪"


def check_hf_endpoint(cfg: Config | None = None) -> CheckItem:
    """检测 HuggingFace 端点可达性（决定能否自动下载模型）。"""
    import requests

    explicit = os.environ.get("HF_ENDPOINT")
    endpoints = [explicit] if explicit else list(HF_MIRRORS)
    for ep in endpoints:
        if not ep:
            continue
        try:
            r = requests.head(ep, timeout=6, allow_redirects=True)
            if r.status_code < 400:
                return CheckItem("模型下载源", True, f"{ep}（可达）", "optional")
        except Exception:  # noqa: BLE001
            continue
    return CheckItem(
        "模型下载源",
        False,
        "HuggingFace 不可达 · 请手动放置模型到 models/ 目录",
        "optional",
    )


def check_asr_model(cfg: Config) -> CheckItem:
    engine = cfg.get("asr.engine", "faster-whisper")
    if engine == "whispercpp":
        path, source = resolve_model_path(cfg, "whispercpp")
    else:
        path, source = resolve_model_path(cfg, "asr")
    if source == "repo":
        return CheckItem(
            "ASR 模型", False, f"本地未找到，将按仓库名下载: {path}", "optional"
        )
    if source == "local-fallback":
        detail = f"{path}  (配置路径不存在，已自动采用本地完整模型)"
    else:
        detail = f"{path}  (来源: {source})"
    return CheckItem("ASR 模型", Path(path).exists(), detail, "optional")


def check_llm(cfg: Config, timeout: float = 4.0) -> CheckItem:
    base = cfg.get("translate.base_url", "")
    if not base:
        return CheckItem("翻译模型服务", False, "未配置 base_url · 仅转录/导出仍可使用", "optional")
    import requests

    url = base.rstrip("/") + "/models"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {cfg.get('translate.api_key', '')}"},
            timeout=timeout,
        )
        if r.status_code == 200:
            try:
                ids = [m.get("id") for m in r.json().get("data", [])][:3]
            except Exception:  # noqa: BLE001
                ids = []
            detail = base + ("  ·  " + ", ".join(filter(None, ids)) if ids else "")
            return CheckItem("翻译模型服务", True, detail, "optional")
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            "翻译模型服务",
            False,
            f"{base} 未连通（{type(exc).__name__}）· 只做转录可不理会",
            "optional",
        )
    return CheckItem("翻译模型服务", False, f"{base} 返回非 200", "optional")


def check_nvenc() -> CheckItem:
    exe = shutil.which("ffmpeg")
    if not exe:
        return CheckItem("NVENC 硬件编码", False, "缺少 ffmpeg", "optional")
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return CheckItem("NVENC 硬件编码", False, str(exc), "optional")
    for enc in ("h264_nvenc", "hevc_nvenc"):
        if enc in out:
            return CheckItem("NVENC 硬件编码", True, f"可用: {enc}", "optional")
    return CheckItem("NVENC 硬件编码", False, "不可用，烧录将回退 libx264", "optional")


def check_models_dir(cfg: Config) -> CheckItem:
    root = cfg.model_root
    return CheckItem(
        "模型目录",
        root.is_dir(),
        f"{root}",
        "required",
    )


def run_startup_checks(cfg: Config) -> list[CheckItem]:
    """跑一遍全部自检，返回有序清单。"""
    return [
        check_ffmpeg(),
        check_ffprobe(),
        check_faster_whisper(),
        check_cuda(),
        check_nvenc(),
        check_models_dir(cfg),
        check_asr_model(cfg),
        check_hf_endpoint(cfg),
        check_llm(cfg),
    ]


def summarize_checks(items: list[CheckItem]) -> tuple[bool, str]:
    """返回 (是否可开工, Markdown 文本)。"""
    blocked = [i for i in items if not i.ok and i.level == "required"]
    ok = not blocked

    lines = ["| 检查项 | 类别 | 状态 | 详情 |", "| :--- | :---: | :---: | :--- |"]
    for it in items:
        kind = "必需" if it.level == "required" else "可选"
        lines.append(f"| {it.name} | {kind} | {_tag(it)} | {it.detail} |")
    lines.append("")

    if ok:
        lines.append("**环境就绪** — 转录与渲染链路可用。翻译链路取决于「翻译模型服务」是否连通。")
    else:
        names = "、".join(i.name for i in blocked)
        lines.append(f"**缺少必需组件：{names}** — 请先补齐再开始。")
    return ok, "\n".join(lines)
