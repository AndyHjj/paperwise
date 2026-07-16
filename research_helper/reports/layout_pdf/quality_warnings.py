from __future__ import annotations

import warnings
from collections.abc import Callable

QualityWarningSink = Callable[[tuple[str, ...]], None]


def notify_quality_warnings(
    message: str | None,
    warning_sink: QualityWarningSink | None,
) -> None:
    if message is None:
        return
    if warning_sink is not None:
        warning_sink((message,))
        return
    try:
        warnings.warn(
            "Bilingual PDF was published with quality warnings:\n" + message,
            UserWarning,
            stacklevel=3,
        )
    except UserWarning:
        pass


def combine_warnings(current: str | None, additional: str) -> str:
    if current is None:
        return additional
    return f"{current}\n{additional}"
