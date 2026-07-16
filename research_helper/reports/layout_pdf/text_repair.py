from __future__ import annotations

import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

import fitz

_DETACHED_ACUTE = re.compile(r"´([A-Za-z])")
_SPLIT_EMAIL = re.compile(
    r"(?P<left><?[A-Za-z0-9._%+]+)-\s+(?P<right>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}>?)"
)
_LATIN_NAME_WORD = re.compile(r"[A-Za-z´'-]+")
_COMPOSED_ACCENT = re.compile(r"[À-ÖØ-öø-ÿ]")
_LEGACY_INLINE_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9@._%+<>'·-]+")


@dataclass(frozen=True, slots=True)
class _PdfWord:
    rectangle: fitz.Rect
    text: str
    block_index: int
    line_index: int


@dataclass(frozen=True, slots=True)
class _TextPatch:
    rectangle: fitz.Rect
    text: str
    font_name: str
    font_size: float
    baseline: float


@dataclass(frozen=True, slots=True)
class _TextSegment:
    text: str
    font_name: str


def repair_latin_typography(translated_pdf: Path) -> int:
    document = fitz.open(translated_pdf)
    repair_count = 0
    try:
        for page in document:
            patches = _page_patches(page)
            if not patches:
                continue
            _apply_patches(page, patches)
            repair_count += len(patches)
        if not repair_count:
            return 0
        with tempfile.NamedTemporaryFile(
            prefix=f".{translated_pdf.stem}.",
            suffix=".text-repair.pdf",
            dir=translated_pdf.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink()
        document.save(temporary_path, garbage=4, deflate=True, deflate_fonts=True)
    finally:
        document.close()
    temporary_path.replace(translated_pdf)
    return repair_count


def _page_patches(page: fitz.Page) -> tuple[_TextPatch, ...]:
    words = _page_words(page)
    patches: list[_TextPatch] = []
    consumed: set[int] = set()
    for index, word in enumerate(words):
        if index in consumed:
            continue
        paired = _email_patch(page, words, index)
        if paired is not None:
            patches.append(paired)
            consumed.update((index, index + 1))
            continue
        accented, positions = _accent_patch(page, words, index)
        if accented is not None:
            patches.append(accented)
            consumed.update(positions)
    patches.extend(_legacy_patches(page, words, consumed))
    return tuple(patches)


def _legacy_patches(
    page: fitz.Page,
    words: tuple[_PdfWord, ...],
    consumed: set[int],
) -> tuple[_TextPatch, ...]:
    patches: list[_TextPatch] = []
    migrated: set[int] = set()
    for index, word in enumerate(words):
        if index in consumed or index in migrated:
            continue
        if _COMPOSED_ACCENT.search(word.text) is None:
            continue
        if any(_uses_cjk_font(char) for char in word.text):
            patch = _make_patch(page, word.rectangle, word.text)
            if patch.font_size > 6.1:
                continue
            unit_width = _text_width(patch.text, patch.font_name, 1.0)
            repaired_size = min(9.0, patch.rectangle.width / max(1.0, unit_width))
            if repaired_size > patch.font_size:
                patches.append(replace(patch, font_size=repaired_size))
                migrated.add(index)
            continue
        aligned = tuple(
            position
            for position, candidate in enumerate(words)
            if position not in consumed
            and position not in migrated
            and candidate.block_index == word.block_index
            and abs(candidate.rectangle.y0 - word.rectangle.y0) <= 0.5
            and abs(candidate.rectangle.y1 - word.rectangle.y1) <= 0.5
            and _LEGACY_INLINE_WORD.fullmatch(candidate.text) is not None
        )
        if len(aligned) < 2:
            continue
        selected = tuple(
            sorted(
                (words[position] for position in aligned),
                key=lambda candidate: candidate.rectangle.x0,
            )
        )
        rectangle = fitz.Rect(selected[0].rectangle)
        for selected_word in selected[1:]:
            rectangle |= selected_word.rectangle
        text = " ".join(selected_word.text for selected_word in selected)
        font_name, font_size, _ = _word_style(page, rectangle)
        if not any(
            right.rectangle.x0 - left.rectangle.x1 > font_size * 0.5
            for left, right in zip(selected, selected[1:])
        ):
            continue
        patches.append(_make_patch(page, rectangle, text))
        migrated.update(aligned)
    return tuple(patches)


def _email_patch(
    page: fitz.Page,
    words: tuple[_PdfWord, ...],
    index: int,
) -> _TextPatch | None:
    if index + 1 >= len(words):
        return None
    left = words[index]
    right = words[index + 1]
    if (
        left.block_index != right.block_index
        or left.line_index != right.line_index
        or not left.text.endswith("-")
    ):
        return None
    trailing = right.text[len(right.text.rstrip("，。")) :]
    right_core = right.text.removesuffix(trailing) if trailing else right.text
    combined = f"{left.text} {right_core}"
    fixed = _SPLIT_EMAIL.sub(r"\g<left>\g<right>", combined)
    if fixed == combined:
        return None
    rectangle = left.rectangle | right.rectangle
    if trailing:
        _, font_size, _ = _word_style(page, right.rectangle)
        rectangle.x1 -= fitz.get_text_length(
            trailing,
            fontname="china-s",
            fontsize=font_size,
        )
    return _make_patch(page, rectangle, fixed)


def _accent_patch(
    page: fitz.Page,
    words: tuple[_PdfWord, ...],
    index: int,
) -> tuple[_TextPatch | None, tuple[int, ...]]:
    word = words[index]
    if "´" not in word.text:
        return None, ()
    if _LATIN_NAME_WORD.fullmatch(word.text) is None:
        fixed = _fix_accents(word.text)
        return _make_patch(page, word.rectangle, fixed), (index,)
    start = index
    while start > 0 and _same_line(words[start - 1], word):
        if _LATIN_NAME_WORD.fullmatch(words[start - 1].text) is None:
            break
        start -= 1
    end = index
    while end + 1 < len(words) and _same_line(words[end + 1], word):
        if _LATIN_NAME_WORD.fullmatch(words[end + 1].text) is None:
            break
        end += 1
    selected = words[start : end + 1]
    rectangle = fitz.Rect(selected[0].rectangle)
    for selected_word in selected[1:]:
        rectangle |= selected_word.rectangle
    fixed = _fix_accents(" ".join(selected_word.text for selected_word in selected))
    return _make_patch(page, rectangle, fixed), tuple(range(start, end + 1))


def _same_line(left: _PdfWord, right: _PdfWord) -> bool:
    return (
        left.block_index == right.block_index
        and left.line_index == right.line_index
    )


def _make_patch(page: fitz.Page, rectangle: fitz.Rect, text: str) -> _TextPatch:
    font_name, font_size, baseline = _word_style(page, rectangle)
    width = _text_width(text, font_name, font_size)
    if width > rectangle.width:
        font_size = max(6.0, font_size * rectangle.width / width)
    return _TextPatch(rectangle, text, font_name, font_size, baseline)


def _text_width(text: str, latin_font: str, font_size: float) -> float:
    return sum(
        fitz.get_text_length(
            segment.text,
            fontname=segment.font_name,
            fontsize=font_size,
        )
        for segment in _text_segments(text, latin_font)
    )


def _word_style(page: fitz.Page, rectangle: fitz.Rect) -> tuple[str, float, float]:
    best_area = 0.0
    selected_font = "tiro"
    selected_size = max(6.0, rectangle.height / 1.34)
    selected_baseline = rectangle.y1 - selected_size * 0.28
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_box = fitz.Rect(span["bbox"])
                intersection = rectangle & span_box
                area = intersection.get_area()
                if area <= best_area:
                    continue
                best_area = area
                font = str(span.get("font", ""))
                selected_font = "tibo" if "bold" in font.lower() or "medi" in font.lower() else "tiro"
                selected_size = float(span.get("size", selected_size))
                selected_baseline = float(span.get("origin", (0.0, selected_baseline))[1])
    return selected_font, selected_size, selected_baseline


def _apply_patches(page: fitz.Page, patches: tuple[_TextPatch, ...]) -> None:
    for patch in patches:
        rectangle = patch.rectangle + (0.1, 0.1, -0.1, -0.1)
        page.add_redact_annot(rectangle, fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0, text=0)
    for patch in patches:
        x_position = patch.rectangle.x0
        for segment in _text_segments(patch.text, patch.font_name):
            page.insert_text(
                (x_position, patch.baseline),
                segment.text,
                fontname=segment.font_name,
                fontsize=patch.font_size,
            )
            x_position += fitz.get_text_length(
                segment.text,
                fontname=segment.font_name,
                fontsize=patch.font_size,
            )


def _page_words(page: fitz.Page) -> tuple[_PdfWord, ...]:
    return tuple(
        _PdfWord(
            rectangle=fitz.Rect(raw[:4]),
            text=str(raw[4]),
            block_index=int(raw[5]),
            line_index=int(raw[6]),
        )
        for raw in page.get_text("words")
    )


def _fix_accents(text: str) -> str:
    return _DETACHED_ACUTE.sub(
        lambda match: unicodedata.normalize("NFC", f"{match.group(1)}\u0301"),
        text,
    )


def _text_segments(text: str, latin_font: str) -> tuple[_TextSegment, ...]:
    segments: list[_TextSegment] = []
    current_text = ""
    current_font = ""
    for char in text:
        font_name = "china-s" if _uses_cjk_font(char) else latin_font
        if current_text and font_name != current_font:
            segments.append(_TextSegment(current_text, current_font))
            current_text = ""
        current_text += char
        current_font = font_name
    if current_text:
        segments.append(_TextSegment(current_text, current_font))
    return tuple(segments)


def _uses_cjk_font(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff" or char in "，。；：（）【】《》"
