from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from research_helper.reports.layout_pdf.fidelity import (
    PdfFidelityError,
    verify_translated_pdf,
)

def _write_pdf(
    path: Path,
    text: str,
    *,
    width: float = 220,
    height: float = 300,
    rotation: int = 0,
) -> Path:
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    font_name = "china-s" if any("\u3400" <= char <= "\u9fff" for char in text) else "helv"
    page.insert_text((20, 30), text, fontname=font_name, fontsize=8)
    page.set_rotation(rotation)
    document.save(path)
    document.close()
    return path


def test_fidelity_rejects_rotation_and_page_box_changes(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "source.pdf",
        "source token 42",
        width=220,
        height=300,
        rotation=90,
    )
    translated = _write_pdf(
        tmp_path / "translated.pdf",
        "中文译文 token 42",
        width=300,
        height=220,
    )

    with pytest.raises(PdfFidelityError, match="rotation|MediaBox|CropBox"):
        verify_translated_pdf(source, translated)


def test_fidelity_rejects_additional_numeric_tokens(tmp_path: Path) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source token 42")
    translated = _write_pdf(
        tmp_path / "translated.pdf",
        "中文 42 99",
    )

    with pytest.raises(PdfFidelityError, match="added numeric tokens"):
        verify_translated_pdf(source, translated)


def test_fidelity_allows_localized_month_number(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "source.pdf",
        "Conference June 21-25, 2025 token 42",
        width=400,
    )
    translated = _write_pdf(
        tmp_path / "translated.pdf",
        "中文会议于2025年6月21-25日举行 token 42",
        width=400,
    )

    report = verify_translated_pdf(source, translated)

    assert report.page_count == 1


def test_fidelity_rejects_additional_image_occurrences(tmp_path: Path) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source token 42")
    translated = fitz.open()
    page = translated.new_page(width=220, height=300)
    page.insert_text((20, 30), "中文译文 token 42", fontname="china-s")
    pixel = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
    pixel.clear_with(255)
    page.insert_image(
        fitz.Rect(20, 60, 40, 80),
        stream=pixel.tobytes("png"),
    )
    translated_path = tmp_path / "translated.pdf"
    translated.save(translated_path)
    translated.close()

    with pytest.raises(PdfFidelityError, match="added an image"):
        verify_translated_pdf(source, translated_path)


def test_fidelity_rejects_overlapping_short_lines(tmp_path: Path) -> None:
    source = _write_pdf(tmp_path / "source.pdf", "source token 42")
    translated = fitz.open()
    page = translated.new_page(width=220, height=300)
    page.insert_text((20, 30), "中文译文 token 42", fontname="china-s")
    page.insert_text(
        (20, 82),
        "短行重叠检测甲乙丙丁戊己庚辛",
        fontname="china-s",
    )
    page.insert_text(
        (22, 84),
        "另一短行重叠甲乙丙丁戊己庚辛",
        fontname="china-s",
    )
    translated_path = tmp_path / "translated.pdf"
    translated.save(translated_path)
    translated.close()

    with pytest.raises(PdfFidelityError, match="overlapping body text"):
        verify_translated_pdf(source, translated_path)


def test_fidelity_rejects_an_english_only_translation(tmp_path: Path) -> None:
    english = "An English paragraph that should have been translated. " * 8 + "token 42"
    source = _write_pdf(tmp_path / "source.pdf", english)
    translated = _write_pdf(tmp_path / "translated.pdf", english)

    with pytest.raises(PdfFidelityError, match="Chinese translation evidence"):
        verify_translated_pdf(source, translated)
