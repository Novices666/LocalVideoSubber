"""字幕样式规范文件管理。

每个样式是一个 JSON 文件，存放在 <project>/styles/ 目录下。
渲染页选择一个样式 profile，转成 render 需要的 style + bilingual 两个 dict。

内置若干预设（首次运行播种到 styles/ 目录），用户可新建 / 编辑 / 删除。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .config import Config, PROJECT_ROOT

DEFAULT_STYLES_DIR = PROJECT_ROOT / "styles"

# profile 的合法字段（与 subtitle/io.py build_ass_header 的字段一一对应）
STYLE_FIELDS = (
    "name", "font_name", "font_size", "source_font_size", "target_font_size",
    "primary_color", "secondary_color", "outline_color", "back_color",
    "outline", "source_outline", "target_outline", "shadow", "margin_v",
    "source_margin_v", "target_margin_v", "order", "orientation",
)


class StyleError(Exception):
    """样式规范文件错误。"""


@dataclass
class StyleProfile:
    """一个字幕样式规范。字段与 ASS 渲染对齐。"""

    name: str = "默认样式"
    font_name: str = "Microsoft YaHei"
    font_size: int = 42
    source_font_size: int = 32
    target_font_size: int = 42
    primary_color: str = "&H00FFFFFF"
    secondary_color: str = "&H000000FF"
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"
    outline: int = 2
    source_outline: int = 2
    target_outline: int = 2
    shadow: int = 1
    margin_v: int = 40
    source_margin_v: int | None = None
    target_margin_v: int | None = None
    order: str = "target_top"          # target_top | source_top
    orientation: str = "landscape"     # landscape | portrait，决定预览画布与排版方向

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StyleProfile":
        kwargs: dict[str, Any] = {}
        for f in STYLE_FIELDS:
            if f in data and data[f] is not None:
                kwargs[f] = data[f]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_render(self) -> tuple[dict, dict]:
        """转成 render 需要的 (style, bilingual) 两个 dict。"""
        style = {
            "font_name": self.font_name,
            "font_size": self.font_size,
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "outline_color": self.outline_color,
            "back_color": self.back_color,
            "outline": self.outline,
            "source_outline": self.source_outline,
            "target_outline": self.target_outline,
            "shadow": self.shadow,
            "margin_v": self.margin_v,
            "source_margin_v": self.source_margin_v,
            "target_margin_v": self.target_margin_v,
        }
        bilingual = {
            "order": self.order,
            "source_font_size": self.source_font_size,
            "target_font_size": self.target_font_size,
        }
        return style, bilingual


# --------------------------------------------------------------------------
# 内置预设
# --------------------------------------------------------------------------
BUILTIN_PROFILES: list[StyleProfile] = [
    StyleProfile(
        name="标准样式",
        font_name="Microsoft YaHei",
        font_size=42, source_font_size=32, target_font_size=42,
        primary_color="&H00FFFFFF", secondary_color="&H000000FF",
        outline_color="&H00000000", back_color="&H80000000",
        outline=2, shadow=1, margin_v=40, order="target_top",
    ),
    StyleProfile(
        name="大字样式（竖屏/手机）",
        font_name="Microsoft YaHei",
        font_size=52, source_font_size=40, target_font_size=52,
        primary_color="&H00FFFFFF", secondary_color="&H000000FF",
        outline_color="&H00000000", back_color="&H80000000",
        outline=3, shadow=1, margin_v=60, order="target_top",
        orientation="portrait",
    ),
    StyleProfile(
        name="简洁小字（大屏观影）",
        font_name="Microsoft YaHei",
        font_size=34, source_font_size=26, target_font_size=34,
        primary_color="&H00FFFFFF", secondary_color="&H000000FF",
        outline_color="&H00000000", back_color="&H80000000",
        outline=1, shadow=1, margin_v=30, order="target_top",
        orientation="landscape",
    ),
    StyleProfile(
        name="竖屏样式（短视频）",
        font_name="Microsoft YaHei",
        font_size=54, source_font_size=42, target_font_size=54,
        primary_color="&H00FFFFFF", secondary_color="&H000000FF",
        outline_color="&H00000000", back_color="&H80000000",
        outline=3, shadow=1, margin_v=90, order="target_top",
        orientation="portrait",
    ),
]


def styles_dir(cfg: Config | None = None) -> Path:
    raw = cfg.get("styles.dir") if cfg else None
    if raw:
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return DEFAULT_STYLES_DIR


def ensure_styles(cfg: Config | None = None) -> Path:
    """确保 styles 目录存在，仅在首次（无播种标记）时写入内置预设。

    用户删除内置预设后不应被自动复活，所以用标记文件记录「已播种」。
    """
    d = styles_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    _seed_builtins_once(d)
    return d


def _seed_builtins_once(d: Path) -> None:
    """首次播种内置预设，之后不再自动补缺失的。"""
    marker = d / ".seeded"
    if marker.exists():
        return
    for profile in BUILTIN_PROFILES:
        target = _path_for(d, profile.name)   # 必须走 sanitize，名称可能含 / 等非法字符
        if not target.exists():
            try:
                _write(profile, target)
            except OSError:
                continue
    try:
        marker.touch()
    except OSError:
        pass


def _sanitize_filename(name: str) -> str:
    stem = "".join(c if c not in '<>:"/\\|?*\x00-\x1f' else "_" for c in name).strip(" .")
    return stem[:80] or "未命名样式"


def _write(profile: StyleProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _path_for(d: Path, name: str) -> Path:
    return d / f"{_sanitize_filename(name)}.json"


def _load_file(path: Path) -> StyleProfile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StyleError(f"样式文件读取失败 {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise StyleError(f"样式文件格式错误 {path.name}：顶层必须是对象")
    profile = StyleProfile.from_dict(data)
    if not profile.name:
        profile.name = path.stem
    return profile


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
def list_styles(cfg: Config | None = None) -> list[dict[str, Any]]:
    """列出所有样式 profile（含内置播种的）。"""
    d = ensure_styles(cfg)
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            profile = _load_file(p)
        except StyleError:
            continue
        item = profile.to_dict()
        item["_file"] = p.name
        out.append(item)
    return out


def get_style(name: str, cfg: Config | None = None) -> StyleProfile:
    """按名称加载一个样式。找不到则回退到默认样式。"""
    d = ensure_styles(cfg)
    path = _path_for(d, name)
    if not path.exists():
        # 允许直接用文件名（含/不含 .json）
        alt = d / name if name.endswith(".json") else path
        if alt.exists():
            return _load_file(alt)
        # 回退默认
        fallback = _path_for(d, BUILTIN_PROFILES[0].name)
        if fallback.exists():
            return _load_file(fallback)
        return BUILTIN_PROFILES[0]
    return _load_file(path)


def save_style(data: dict[str, Any], cfg: Config | None = None) -> dict[str, Any]:
    """新建或更新一个样式（按 name 定位）。返回落盘后的 profile dict。"""
    d = ensure_styles(cfg)
    name = str(data.get("name") or "").strip()
    if not name:
        raise StyleError("样式名不能为空")
    profile = StyleProfile.from_dict(data)
    profile.name = name
    _write(profile, _path_for(d, name))
    return profile.to_dict()


def delete_style(name: str, cfg: Config | None = None) -> bool:
    """删除一个样式。返回是否删除成功。"""
    d = ensure_styles(cfg)
    path = _path_for(d, name)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def reset_builtins(cfg: Config | None = None) -> int:
    """重新播种内置预设（覆盖同名文件），返回写入数量。同时更新播种标记。"""
    d = styles_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for profile in BUILTIN_PROFILES:
        _write(profile, _path_for(d, profile.name))
        n += 1
    try:
        (d / ".seeded").touch()
    except OSError:
        pass
    return n
