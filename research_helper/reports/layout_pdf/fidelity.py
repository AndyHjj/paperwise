from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from research_helper.reports.layout_pdf.numeric_fidelity import (
    compare_numeric_tokens,
)

_PLACEHOLDERS = (
    "See translation appendix",
    "Paperwise translation appendix",
    "translation failed",
    "�",
)


class PdfFidelityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FidelityReport:
    page_count: int
    image_occurrences_checked: int
    numeric_tokens_checked: int
    minimum_cjk_font_size: float | None
    cjk_characters_checked: int


def verify_translated_pdf(
    source_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: int | None = None,
) -> FidelityReport:
    source = fitz.open(source_pdf)
    translated = fitz.open(translated_pdf)
    try:
        expected_pages = min(source.page_count, max_pages) if max_pages else source.page_count
        issues: list[str] = []
        if translated.page_count != expected_pages:
            raise PdfFidelityError(
                f"page count mismatch: expected {expected_pages}, got {translated.page_count}"
            )

        image_count = 0
        numeric_count = 0
        cjk_sizes: list[float] = []
        source_letters = 0
        cjk_count = 0
        for index in range(expected_pages):
            source_page = source[index]
            target_page = translated[index]
            _check_page_geometry(index, source_page, target_page, issues)
            target_text = target_page.get_text()
            source_text = source_page.get_text()
            source_letters += len(
                re.findall(r"[A-Za-z]", re.sub(r"https?://\S+", "", source_text))
            )
            cjk_count += sum(_is_cjk(char) for char in target_text)
            _check_placeholders(index, target_text, issues)
            checked_tokens, numeric_issues = compare_numeric_tokens(
                source_text,
                target_text,
            )
            numeric_count += checked_tokens
            issues.extend(
                f"page {index + 1} {issue}" for issue in numeric_issues
            )
            image_count += _check_images(index, source_page, target_page, issues)
            _check_page_not_blank(index, source_page, target_page, issues)
            _check_body_text_overlaps(index, source_page, target_page, issues)
            cjk_sizes.extend(_cjk_font_sizes(target_page))

        if cjk_sizes and min(cjk_sizes) < 5.9:
            issues.append(
                f"CJK font size falls below 5.9pt (minimum {min(cjk_sizes):.2f}pt)"
            )
        if source_letters >= 20 and cjk_count == 0:
            issues.append("no Chinese translation evidence was found in translated text")
        if issues:
            raise PdfFidelityError("PDF fidelity check failed:\n- " + "\n- ".join(issues))
        return FidelityReport(
            page_count=expected_pages,
            image_occurrences_checked=image_count,
            numeric_tokens_checked=numeric_count,
            minimum_cjk_font_size=min(cjk_sizes) if cjk_sizes else None,
            cjk_characters_checked=cjk_count,
        )
    finally:
        translated.close()
        source.close()


def _check_page_geometry(
    page_index: int,
    source_page: fitz.Page,
    target_page: fitz.Page,
    issues: list[str],
) -> None:
    tolerance = 0.25
    page_boxes = (
        ("MediaBox", source_page.mediabox, target_page.mediabox),
        ("CropBox", source_page.cropbox, target_page.cropbox),
    )
    for label, source_box, target_box in page_boxes:
        if any(abs(left - right) > tolerance for left, right in zip(source_box, target_box)):
            issues.append(
                f"page {page_index + 1} {label} changed from {source_box} to {target_box}"
            )
    if source_page.rotation != target_page.rotation:
        issues.append(
            f"page {page_index + 1} rotation changed from "
            f"{source_page.rotation} to {target_page.rotation}"
        )
    if (
        abs(source_page.rect.width - target_page.rect.width) > tolerance
        or abs(source_page.rect.height - target_page.rect.height) > tolerance
    ):
        issues.append(
            f"page {page_index + 1} size changed from {source_page.rect} to {target_page.rect}"
        )


def _check_placeholders(page_index: int, text: str, issues: list[str]) -> None:
    folded = text.casefold()
    found = [placeholder for placeholder in _PLACEHOLDERS if placeholder.casefold() in folded]
    if found:
        issues.append(
            f"page {page_index + 1} contains placeholder text: {', '.join(found)}"
        )


def _check_images(
    page_index: int,
    source_page: fitz.Page,
    target_page: fitz.Page,
    issues: list[str],
) -> int:
    source_images = source_page.get_image_info(xrefs=True)
    target_images = list(target_page.get_image_info(xrefs=True))
    unmatched = list(range(len(target_images)))
    for source_image in source_images:
        match = next(
            (
                position
                for position in unmatched
                if _same_image_occurrence(source_image, target_images[position])
            ),
            None,
        )
        if match is None:
            issues.append(
                f"page {page_index + 1} lost or moved an image at {source_image.get('bbox')}"
            )
        else:
            unmatched.remove(match)
    for position in unmatched:
        issues.append(
            f"page {page_index + 1} added an image at "
            f"{target_images[position].get('bbox')}"
        )
    return len(source_images)


def _same_image_occurrence(source: dict, target: dict) -> bool:
    if source.get("digest") != target.get("digest"):
        return False
    if source.get("width") != target.get("width") or source.get("height") != target.get("height"):
        return False
    source_bbox = fitz.Rect(source["bbox"])
    target_bbox = fitz.Rect(target["bbox"])
    return all(abs(left - right) <= 0.25 for left, right in zip(source_bbox, target_bbox))


def _check_page_not_blank(
    page_index: int,
    source_page: fitz.Page,
    target_page: fitz.Page,
    issues: list[str],
) -> None:
    source_ink = _ink_ratio(source_page)
    target_ink = _ink_ratio(target_page)
    minimum = max(0.002, source_ink * 0.2)
    if source_ink >= 0.002 and target_ink < minimum:
        issues.append(
            f"page {page_index + 1} is nearly blank "
            f"(ink ratio {target_ink:.4f}, expected at least {minimum:.4f})"
        )


def _ink_ratio(page: fitz.Page) -> float:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY)
    samples = pixmap.samples
    ink_pixels = sum(value < 245 for value in samples)
    return ink_pixels / max(1, len(samples))


def _cjk_font_sizes(page: fitz.Page) -> list[float]:
    sizes: list[float] = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", ""))
                count = sum(_is_cjk(char) for char in text)
                sizes.extend([float(span.get("size", 0.0))] * count)
    return sizes


def _check_body_text_overlaps(
    page_index: int,
    source_page: fitz.Page,
    target_page: fitz.Page,
    issues: list[str],
) -> None:
    source_count = _body_text_overlap_count(source_page)
    target_count = _body_text_overlap_count(target_page)
    if target_count > source_count:
        issues.append(
            f"page {page_index + 1} introduced overlapping body text "
            f"({target_count} target pairs versus {source_count} source pairs)"
        )


def _body_text_overlap_count(page: fitz.Page) -> int:
    lines: list[tuple[fitz.Rect, str]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(
                str(span.get("text", "")) for span in line.get("spans", [])
            ).strip()
            if text:
                lines.append((fitz.Rect(line["bbox"]), text))

    overlap_count = 0
    for index, (left_box, left_text) in enumerate(lines):
        for right_box, right_text in lines[index + 1 :]:
            combined_text = f"{left_text} {right_text}".lower()
            if any(
                marker in combined_text
                for marker in ("://", "github.com", "doi.org", "arxiv.org")
            ):
                continue
            intersection = left_box & right_box
            if intersection.is_empty or intersection.width < 6:
                continue
            height_ratio = intersection.height / max(
                1.0,
                min(left_box.height, right_box.height),
            )
            width_ratio = intersection.width / max(
                1.0,
                min(left_box.width, right_box.width),
            )
            if height_ratio >= 0.35 and width_ratio >= 0.2:
                overlap_count += 1
    return overlap_count


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
