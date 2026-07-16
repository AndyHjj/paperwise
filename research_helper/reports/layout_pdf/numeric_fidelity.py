from __future__ import annotations

import re
from collections import Counter

_PROTECTED_TOKEN = re.compile(r"(?<!\d)\d+(?:\.\d+)?%?(?!\d)")
_TARGET_MONTH = re.compile(r"(?<!\d)(1[0-2]|[1-9])\s*月")
_SOURCE_MONTHS = (
    (re.compile(r"(?<![A-Za-z])(?:January|Jan\.)(?![A-Za-z])"), "1"),
    (re.compile(r"(?<![A-Za-z])(?:February|Feb\.)(?![A-Za-z])"), "2"),
    (re.compile(r"(?<![A-Za-z])(?:March|Mar\.)(?![A-Za-z])"), "3"),
    (re.compile(r"(?<![A-Za-z])(?:April|Apr\.)(?![A-Za-z])"), "4"),
    (re.compile(r"(?<![A-Za-z])May(?![A-Za-z])"), "5"),
    (re.compile(r"(?<![A-Za-z])(?:June|Jun\.)(?![A-Za-z])"), "6"),
    (re.compile(r"(?<![A-Za-z])(?:July|Jul\.)(?![A-Za-z])"), "7"),
    (re.compile(r"(?<![A-Za-z])(?:August|Aug\.)(?![A-Za-z])"), "8"),
    (re.compile(r"(?<![A-Za-z])(?:September|Sept?\.)(?![A-Za-z])"), "9"),
    (re.compile(r"(?<![A-Za-z])(?:October|Oct\.)(?![A-Za-z])"), "10"),
    (re.compile(r"(?<![A-Za-z])(?:November|Nov\.)(?![A-Za-z])"), "11"),
    (re.compile(r"(?<![A-Za-z])(?:December|Dec\.)(?![A-Za-z])"), "12"),
)


def compare_numeric_tokens(
    source_text: str,
    target_text: str,
) -> tuple[int, tuple[str, ...]]:
    source_tokens = Counter(protected_numeric_tokens(source_text))
    target_tokens = Counter(protected_numeric_tokens(target_text))
    missing = source_tokens - target_tokens
    added = (target_tokens - source_tokens) - _localized_months(
        source_text,
        target_text,
    )
    issues: list[str] = []
    if missing:
        preview = ", ".join(
            f"{token}×{count}" for token, count in missing.most_common(8)
        )
        issues.append(f"lost numeric tokens: {preview}")
    if added:
        preview = ", ".join(
            f"{token}×{count}" for token, count in added.most_common(8)
        )
        issues.append(f"added numeric tokens: {preview}")
    return sum(source_tokens.values()), tuple(issues)


def protected_numeric_tokens(text: str) -> list[str]:
    return _PROTECTED_TOKEN.findall(text)


def _localized_months(source_text: str, target_text: str) -> Counter[str]:
    source_months: Counter[str] = Counter()
    for pattern, number in _SOURCE_MONTHS:
        source_months[number] += len(pattern.findall(source_text))
    target_months = Counter(_TARGET_MONTH.findall(target_text))
    return source_months & target_months
