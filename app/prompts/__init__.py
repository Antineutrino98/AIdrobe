"""Vision prompt 的版本化管理 + 运行时注入（M2）。

设计依据（用户 2026-11-05 拍板）
--------------------------------
`app/attribute_dimensions.py` 是唯一的枚举来源，M2 不允许硬编码维度值。
所以 `vision_extraction.md` 里只放占位符，真正发给视觉模型的 system prompt
由 `build_vision_system_prompt()` 在运行时把 10 个扩展维度、48 个 category
合法值注入进去。以后加维度/加品类只改那一个文件，prompt 自动同步。

关于 M2.md「必须原样使用」
--------------------------
注入后生成的文本与 M2.md 给出的 system prompt **内容一致**（同样的键、同样的值、
同样的 Rules），只是取值不再抄死在 .md 里。M2.md 省略了 12 个字段的说明
（原文写「与原文完全一致，不重复」但项目里没有那份原文），
这里按 M1.md 的字段定义逐条补齐；若你手上有原始说明，直接替换模板里的
「Field notes」段落即可，不影响代码。

另外按 M2 要求补充了两个字段说明（属于「只允许补充字段说明」的范围）：
`category_confidence` 与 `category_candidates`（用户确认让 AI 多猜一个候选品类）。

安全：本文件与 .md 模板都不包含任何 API key，key 一律从环境变量读取。
"""
from __future__ import annotations

import contextvars
from pathlib import Path

from app.attribute_dimensions import ATTRIBUTE_DIMENSIONS, CATEGORY_VALUES

_TEMPLATE_PATH: Path = Path(__file__).parent / "vision_extraction.md"
_RETRIEVAL_TEMPLATE_PATH: Path = Path(__file__).parent / "retrieval_plan.md"

ATTRIBUTES_PLACEHOLDER: str = "{{ATTRIBUTE_DIMENSIONS_BLOCK}}"
CATEGORIES_PLACEHOLDER: str = "{{CATEGORY_VALUES_BLOCK}}"
COUNTS_PLACEHOLDER: str = "{{CATEGORY_COUNTS_BLOCK}}"


def _render_attribute_dimensions() -> str:
    """键 + 合法值 + 中文说明。

    中文说明不是冗余：模型要靠它理解 slim / tapered / notched 这类术语的
    真实含义，去掉会明显降低属性判断准确率。
    """
    return "\n".join(
        f"  {key}: " + " | ".join(f'"{value}"' for value in spec["values"])
        + f"  # {spec['label_zh']}"
        for key, spec in ATTRIBUTE_DIMENSIONS.items()
    )


def _render_categories() -> str:
    """key — 中文标签。中文标签帮助模型区分近义品类（如 shirt 与 blazer）。"""
    return "\n".join(
        f"  - {key} — {label_zh}" for key, label_zh in CATEGORY_VALUES.items()
    )


def build_vision_system_prompt() -> str:
    """读取版本化模板并注入两份枚举，返回完整的 system prompt。"""
    template: str = _TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt: str = template.replace(
        ATTRIBUTES_PLACEHOLDER, _render_attribute_dimensions()
    ).replace(CATEGORIES_PLACEHOLDER, _render_categories())
    if ATTRIBUTES_PLACEHOLDER in prompt or CATEGORIES_PLACEHOLDER in prompt:
        raise RuntimeError("vision prompt 模板存在未替换的占位符")
    return prompt


def _render_category_counts(category_counts: dict[str, int]) -> str:
    if not category_counts:
        return "  (wardrobe is empty)"
    return "\n".join(
        f"  - {key}: {count}" for key, count in sorted(category_counts.items())
    )


def build_retrieval_plan_system_prompt(
    category_counts: dict[str, int], dress_code_block: str = ""
) -> str:
    """U1：注入各品类数量与（工作场景的）着装要求。

    全局约束策略不需要 48 类清单和属性维度 —— 那是按槽位/条件检索时才用得上的，
    现在 planner 只给范围，具体品类判断归搭配模型。
    """
    template: str = _RETRIEVAL_TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt: str = (
        template.replace(COUNTS_PLACEHOLDER, _render_category_counts(category_counts))
        .replace("{{DRESS_CODE_BLOCK}}", dress_code_block or "（无）")
    )
    # 旧模板里可能残留维度/品类占位符，直接去掉，避免把 48 类又塞回去。
    prompt = prompt.replace(ATTRIBUTES_PLACEHOLDER, "").replace(
        CATEGORIES_PLACEHOLDER, ""
    )
    for placeholder in (COUNTS_PLACEHOLDER, "{{DRESS_CODE_BLOCK}}"):
        if placeholder in prompt:
            raise RuntimeError("retrieval prompt 模板存在未替换的占位符")
    return prompt + language_instruction()


# ------------------------------------------------------- M4 / M5 plain prompts
# 这些 prompt 是纯文本（不含需要注入的枚举），按名字读取即可。
# 白名单避免拼出任意路径；文件名与各模块「交付物」一一对应。
_PLAIN_PROMPTS: frozenset[str] = frozenset(
    {"duplicate", "purchase_decision", "gap_summary", "preference_rewrite"}
)

# 偏好为空时注入给 M3/M4 的占位文本（M5.md「边界」）。
EMPTY_PREFERENCE_TEXT: str = "No preference history yet."


def format_preference_block(preference_text: str | None) -> str:
    """M5 Step 4：把偏好拼成注入 M3/M4 的固定格式。

    - 空字符串/None 一律用 "No preference history yet."，不让模型看到空值。
    - 文案与 M5.md 给出的格式逐字一致。
    """
    text: str = (preference_text or "").strip() or EMPTY_PREFERENCE_TEXT
    # 占位句本身以句号结尾；统一去掉末尾句号，避免拼出 "yet.."。
    text = text.rstrip(".。")
    return (
        f"User's established style preference: {text}. "
        "This should inform but not override context-specific requirements."
    )


# ------------------------------------------------------- 界面语言（21:56 反馈）
# 英文界面时，模型生成的理由 / 结论也要用英文，否则界面会出现中英夹杂。
# 用 contextvar 而不是全局变量：每个 HTTP 请求各自独立，互不串味。
_UI_LANGUAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aidrobe_ui_language", default="zh"
)

_ENGLISH_RULE: str = (
    "\n\nLANGUAGE RULE (highest priority): The user interface is in English. "
    "Write ALL free-text prose in English only. Keep JSON keys, enum/identifier "
    "values, garment ids and numbers exactly as specified. Never mix Chinese "
    "and English inside one sentence."
)

# 00:51 反馈：中文界面里出现了英文的 layering_hint / style_direction。
# 只靠模板里一句「中文一句话」压不住模型，两种语言都显式钉死。
_CHINESE_RULE: str = (
    "\n\n语言规则（最高优先级）：当前界面是简体中文。所有自由文本字段"
    "（layering_hint、style_direction、avoid_notes、reasoning、结论等）"
    "必须全部用简体中文书写，不得夹入英文单词或短语；JSON 键名、枚举值、"
    "garment_id 与数字保持原样。"
)


def set_ui_language(language: str | None) -> None:
    """由 HTTP 中间件按请求头调用；只接受 en，其余一律按中文处理。"""
    _UI_LANGUAGE.set("en" if (language or "").strip().lower() == "en" else "zh")


def language_instruction() -> str:
    """两种模式都给明确语言指令，杜绝中英夹杂。"""
    return _ENGLISH_RULE if _UI_LANGUAGE.get() == "en" else _CHINESE_RULE


def load_prompt(name: str) -> str:
    """读取版本化 system prompt；名字不在白名单内直接报错。"""
    if name not in _PLAIN_PROMPTS:
        raise ValueError(
            f"未知 prompt: {name!r}；允许的取值：{sorted(_PLAIN_PROMPTS)}"
        )
    path: Path = Path(__file__).parent / f"{name}.md"
    return path.read_text(encoding="utf-8").strip() + language_instruction()
