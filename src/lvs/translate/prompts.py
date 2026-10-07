"""字幕翻译提示词模板。

设计要点：
1. 编号输出 + JSON，便于程序回填，杜绝串行。
2. 给上下文（上一批末尾几条），保证代词/术语连贯。
3. 强制"字幕体"：不解释、不加标点冗余、控制长度。
4. 术语表强制替换。
"""
from __future__ import annotations

# --------------------------------------------------------------------------
LANG_NAMES = {
    "zh": "简体中文", "zh-cn": "简体中文", "zh-hans": "简体中文",
    "zh-tw": "繁体中文", "zh-hant": "繁体中文",
    "en": "English", "ja": "日本語", "ko": "한국어",
    "fr": "Français", "de": "Deutsch", "es": "Español",
    "ru": "Русский", "pt": "Português", "it": "Italiano",
    "ar": "العربية", "th": "ไทย", "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia", "hi": "हिन्दी", "tr": "Türkçe",
    "auto": "自动识别",
}

# 目标语言风格提示：不同语言的字幕习惯不一样
STYLE_HINTS = {
    "zh": "译文要口语化、简洁，符合中文字幕习惯。单条尽量控制在 20 个汉字以内（确有需要可到 30）。",
    "zh-cn": "译文要口语化、简洁，符合中文字幕习惯。单条尽量控制在 20 个汉字以内（确有需要可到 30）。",
    "en": "Keep each line under ~45 characters where possible. Natural conversational English.",
    "ja": "自然な日本語の字幕にしてください。1行は全角20文字程度まで。",
    "ko": "자연스러운 한국어 자막으로, 한 줄에 20자 내외로 맞춰주세요.",
}


def lang_name(code: str) -> str:
    if not code:
        return "自动识别"
    return LANG_NAMES.get(code.lower(), code)


# --------------------------------------------------------------------------
SYSTEM_TEMPLATE = """你是一位专业的影视字幕译者，精通{src}到{tgt}的翻译。

你的任务：把视频字幕逐条翻译成{tgt}。

必须遵守的规则：
1. 逐条翻译，条数与输入**严格一致**，不增不减、不合并、不拆分。
2. 只输出译文本身。不要添加任何解释、注释、音译原文、括号说明。
3. 保持口语化、自然流畅，符合{tgt}字幕的表达习惯，不要逐字硬译。
4. 参考「前文」保持人称、称谓、术语、语气的一致性。
5. 如果某条是语气词、拟声词或无实义内容，译成{tgt}中对应的自然说法；若确实无法翻译，原样保留。
6. 人名、品牌名、专有名词按「术语表」处理；术语表没有的，优先音译并保持全篇统一。
7. 字幕要短。{style}
8. 输出格式：严格的 JSON 对象，形如 {{"1": "译文一", "2": "译文二"}}

只输出 JSON，不要有任何额外文字。"""


def build_system_prompt(
    source_lang: str,
    target_lang: str,
    glossary: dict[str, str] | None = None,
    custom: str = "",
) -> str:
    if custom.strip():
        return custom.strip()

    src = lang_name(source_lang)
    tgt = lang_name(target_lang)
    style = STYLE_HINTS.get((target_lang or "").lower(), "")

    parts = [SYSTEM_TEMPLATE.format(src=src, tgt=tgt, style=style).strip()]

    if glossary:
        lines = [f"  {k} → {v}" for k, v in glossary.items() if k and v]
        if lines:
            parts.append("\n术语表（必须严格遵守）：\n" + "\n".join(lines))

    return "\n\n".join(parts)


# --------------------------------------------------------------------------
def build_user_prompt(
    items: list[tuple[int, str]],
    context: list[tuple[int, str, str]] | None = None,
) -> str:
    """构造用户消息。

    items   : [(序号, 原文), ...]  本批待翻译
    context : [(序号, 原文, 译文), ...]  上一批末尾若干条，仅供参照，不要翻译它们
    """
    blocks: list[str] = []

    if context:
        ctx_lines = []
        for idx, src, tgt in context:
            if not src.strip():
                continue
            ctx_lines.append(f"[{idx}] 原文：{src}")
            if tgt:
                ctx_lines.append(f"[{idx}] 译文：{tgt}")
        if ctx_lines:
            blocks.append(
                "前文（仅供理解上下文，**不要翻译这部分**）：\n" + "\n".join(ctx_lines)
            )

    numbered = "\n".join(f"[{i}] {text}" for i, text in items)
    blocks.append(
        "请翻译以下字幕，按编号返回 JSON：\n"
        f"{numbered}\n\n"
        f'输出格式：{{"1": "…", "2": "…"}}（共 {len(items)} 条，编号必须与输入一致）'
    )
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
def build_retry_prompt(items: list[tuple[int, str]], missing: list[int]) -> str:
    """对齐失败时的补救提示。"""
    numbered = "\n".join(f"[{i}] {t}" for i, t in items if i in set(missing))
    return (
        f"上一次输出缺少以下编号的翻译：{sorted(missing)}。\n"
        "请**只**翻译这些条目，仍然按 JSON 编号返回：\n"
        f"{numbered}\n\n"
        f'输出格式：{{"1": "…", "2": "…"}}'
    )
