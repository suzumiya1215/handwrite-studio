"""
字符集常量与文件名双向映射。

设计依据：
- 数据格式规范 §4（字符编码映射表）、§4.4（字符集常量）
- 模块设计说明书 §4.4、§6.3（契约 C9 / C10 / C11）
- 需求文档 §2.2 字符集边界（12 个字符，超范围跳过并提示）

铁律：
- C9  ：权威字符集只有这里一处，别处不许再定义
- C10 ：超出字符集的内容不报错、不静默丢弃，走 skipped_chars 计数
- C11 ：字符 → 目录名的映射只有这里一个实现，禁止别处硬编码 "comma" / "dot"
- N4  ：不出现硬编码的字符目录名
- N10 ：目录名必须是 ASCII（跨平台解压安全）
"""

from __future__ import annotations

from .errors import UnknownCharacterError

# ─────────────────────────────────────────────────────────────
# 字符集定义
# ─────────────────────────────────────────────────────────────

#: 数字 0-9，共 10 个
CHARSET_DIGITS: list[str] = list("0123456789")

#: v1 启用的标点：只要逗号和句点
CHARSET_PUNCT: list[str] = [",", "."]

#: ★ v1 的唯一权威字符集，恰好 12 个，顺序固定（先数字后标点）
CHARSET_V1: list[str] = CHARSET_DIGITS + CHARSET_PUNCT

# ── 以下为预留类别：v1 不启用，但枚举表保留（删了会让历史字形库无法反解）──

CHARSET_UPPER: list[str] = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
CHARSET_LOWER: list[str] = [chr(c) for c in range(ord("a"), ord("z") + 1)]
CHARSET_CJK_PUNCT: list[str] = ["。", "，", "、", "；", "：", "？", "！", "（", "）", "《", "》"]

#: 类别 → 字符列表。解析层用它反解 manifest 里的 charset 字段
CHARSET_MAP: dict[str, list[str]] = {
    "digits": CHARSET_DIGITS,      # ✅ v1 启用
    "punct": CHARSET_PUNCT,        # ✅ v1 启用
    "upper": CHARSET_UPPER,        # ⬜ 预留
    "lower": CHARSET_LOWER,        # ⬜ 预留
    "cjk_punct": CHARSET_CJK_PUNCT,  # ⬜ 预留
}

#: v1 实际启用的类别
CHARSET_ENABLED_V1: list[str] = ["digits", "punct"]

#: 官方推荐书写顺序（引导用户按此顺序写在小抄上）
WRITING_ORDER: list[str] = CHARSET_V1

#: 供错误提示使用的可读清单
CHARSET_HUMAN = "0 1 2 3 4 5 6 7 8 9 , ."


# ─────────────────────────────────────────────────────────────
# 字符 → 目录名（ASCII，跨平台安全）
# ─────────────────────────────────────────────────────────────

#: 特殊字符的目录名。数字直接用自己，所以不必列在这里
_SPECIAL_DIRNAMES: dict[str, str] = {
    ",": "comma",
    ".": "dot",
    # ── 以下为预留，v1 不用，但保留实现以便将来扩展 ──
    "!": "excl", "?": "quest", ":": "colon", ";": "semi",
    "-": "hyphen", "_": "under", "+": "plus", "=": "equal",
    "'": "apos", '"': "quote", "/": "slash", "\\": "backslash",
    "(": "lparen", ")": "rparen", "[": "lbrack", "]": "rbrack",
    "＜": "lt", "＞": "gt", "%": "percent", "*": "asterisk",
    "@": "at", "#": "hash", "&": "amp", "$": "dollar",
    "。": "cjk_period", "，": "cjk_comma", "、": "cjk_dun",
    "；": "cjk_semi", "：": "cjk_colon", "？": "cjk_quest",
    "！": "cjk_excl", "（": "cjk_lparen", "）": "cjk_rparen",
    "《": "cjk_langle", "》": "cjk_rangle",
}

#: 反向表：目录名 → 字符
_DIRNAME_TO_CHAR: dict[str, str] = {
    dirname: ch for ch, dirname in _SPECIAL_DIRNAMES.items()
}
# 数字补进去
for _d in CHARSET_DIGITS:
    _DIRNAME_TO_CHAR[_d] = _d


def char_to_dirname(ch: str) -> str:
    """字符 → 目录名。

    >>> char_to_dirname("3")
    '3'
    >>> char_to_dirname(",")
    'comma'

    Raises:
        UnknownCharacterError: 该字符没有定义目录名（即不在支持范围内）。
    """
    if not ch:
        raise UnknownCharacterError(ch, allowed=CHARSET_HUMAN, detail="空字符串")
    if ch in _SPECIAL_DIRNAMES:
        return _SPECIAL_DIRNAMES[ch]
    if ch.isdigit() and ch in CHARSET_DIGITS:
        return ch
    raise UnknownCharacterError(ch, allowed=CHARSET_HUMAN)


def dirname_to_char(dirname: str) -> str:
    """目录名 → 字符。char_to_dirname 的逆运算。

    Raises:
        UnknownCharacterError: 目录名不认识。
    """
    if dirname in _DIRNAME_TO_CHAR:
        return _DIRNAME_TO_CHAR[dirname]
    raise UnknownCharacterError(
        dirname, allowed=CHARSET_HUMAN, detail=f"未知目录名 {dirname!r}"
    )


def has_dirname(ch: str) -> bool:
    """该字符是否已定义目录名（即是否已纳入支持范围）。"""
    try:
        char_to_dirname(ch)
        return True
    except UnknownCharacterError:
        return False


# ─────────────────────────────────────────────────────────────
# 成员判断与切分
# ─────────────────────────────────────────────────────────────


def is_supported(ch: str) -> bool:
    """该字符是否在 v1 字符集内。"""
    return ch in CHARSET_V1


def filter_supported(text: str) -> tuple[str, list[str]]:
    """把文本按是否支持拆开。

    Returns:
        (支持的字符拼接串, 不支持的字符列表（去重、保持出现顺序）)
    """
    ok_chars: list[str] = []
    bad: list[str] = []
    for ch in text:
        if is_supported(ch):
            ok_chars.append(ch)
        elif ch not in bad:
            bad.append(ch)
    return "".join(ok_chars), bad


def split_by_support(text: str) -> tuple[str, list[str]]:
    """filter_supported 的别名，语义更贴近测试用例里的用法。"""
    return filter_supported(text)


def count_unsupported(text: str) -> int:
    """统计文本里不支持字符的总个数（含重复），供 skipped_chars 计数用。"""
    return sum(1 for ch in text if not is_supported(ch))


def unsupported_counter(texts) -> dict[str, int]:
    """把一批文本里的不支持字符统计成 {字符: 次数}。

    用于 FillService 汇总 skipped_chars。
    """
    counter: dict[str, int] = {}
    for t in texts:
        for ch in t or "":
            if not is_supported(ch):
                counter[ch] = counter.get(ch, 0) + 1
    return counter


# ─────────────────────────────────────────────────────────────
# 校验
# ─────────────────────────────────────────────────────────────


def validate_charset_field(charset) -> tuple[list[str], list[str]]:
    """校验 manifest 里的 charset 字段。

    Returns:
        (有效的类别列表, 警告列表)
    """
    if not isinstance(charset, list):
        return list(CHARSET_ENABLED_V1), ["charset 字段格式不对，已按 v1 默认值处理"]
    warnings: list[str] = []
    valid: list[str] = []
    for cat in charset:
        if cat in CHARSET_MAP:
            valid.append(cat)
        else:
            warnings.append(f"charset 里有不认识的类别「{cat}」，已忽略")
    if not valid:
        valid = list(CHARSET_ENABLED_V1)
        warnings.append("charset 为空，已按 v1 默认值处理")
    return valid, warnings


def enabled_chars(charset: list[str] | None = None) -> list[str]:
    """由 charset 类别列表推出实际字符集。缺省用 v1 全部。"""
    if not charset:
        return list(CHARSET_V1)
    out: list[str] = []
    for cat in charset:
        for ch in CHARSET_MAP.get(cat, []):
            if ch not in out:
                out.append(ch)
    return out


# ─────────────────────────────────────────────────────────────
# 自检（模块导入时跑一次，防止手滑改坏常量）
# ─────────────────────────────────────────────────────────────

assert len(CHARSET_V1) == 12, "★ v1 字符集必须是 12 个"
assert CHARSET_V1[:10] == CHARSET_DIGITS, "顺序必须是先数字"
assert CHARSET_V1[10:] == [",", "."], "后两个必须是 , 和 ."
assert all(char_to_dirname(c).isascii() for c in CHARSET_V1), "目录名必须全 ASCII"
assert all(dirname_to_char(char_to_dirname(c)) == c for c in CHARSET_V1), "双向映射必须自洽"
