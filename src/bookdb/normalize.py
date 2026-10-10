"""字段规范化 —— 输入容错、统一入库格式。

见 PRD：
  §5.1  版次只存纯数字字符串，显示时格式化为「第N版」
  §8.2  --year 接受 单年 / 闭区间 / 开区间
  §23.1 ISBN 去除连字符与空格
"""

from __future__ import annotations

import re
import warnings

# 中文数字 → 阿拉伯数字（用于「第一版」这类输入，取 1..20）
_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}


# ------------------------------------------------------------------ edition
def normalize_edition(raw: object | None) -> str | None:
    """把版次规范化为纯数字字符串。

    "第2版" / "2版" / "2" / "第二版" → "2"
    提取不到数字时原样保留（去空白）并给出警告。
    空输入 → None。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    if text.isdigit():
        return text

    m = re.search(r"\d+", text)
    if m:
        return m.group(0)

    # 中文数字兜底
    for cn in sorted(_CN_DIGITS, key=len, reverse=True):
        if cn in text:
            return str(_CN_DIGITS[cn])

    warnings.warn(f"无法从版次 {text!r} 中提取数字，将原样保存", stacklevel=2)
    return text


# ---------------------------------------------------------------------- isbn
def normalize_isbn(raw: object | None) -> str | None:
    """去除 ISBN 中的连字符与空格（见 §23.1）。"""
    if raw is None:
        return None
    text = re.sub(r"[\s\-]", "", str(raw))
    return text or None


# ---------------------------------------------------------------------- year
def parse_year_range(value: object) -> tuple[int | None, int | None]:
    """解析 --year 参数，返回闭区间 (start, end)。

    "2019"        → (2019, 2019)
    "1800-1900"   → (1800, 1900)
    "1800-"       → (1800, None)   起年及以后
    "-1900"       → (None, 1900)   止年及以前

    非法输入抛 ValueError，绝不做静默猜测。
    """
    text = str(value).strip()
    if not text:
        raise ValueError("年份不能为空")

    if "-" in text:
        head, _, tail = text.partition("-")
        head, tail = head.strip(), tail.strip()
        if not head and not tail:
            raise ValueError(f"非法年份范围：{text!r}")
        start = int(head) if head else None
        end = int(tail) if tail else None
    else:
        start = end = int(text)

    if start is not None and end is not None and start > end:
        raise ValueError(f"起始年份不能大于结束年份：{text!r}")
    return start, end


def normalize_year(raw: object | None) -> int | None:
    """把出版年份规范化为 int；空输入 → None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    m = re.search(r"-?\d+", text)
    if not m:
        raise ValueError(f"无法解析出版年份：{raw!r}")
    return int(m.group(0))
