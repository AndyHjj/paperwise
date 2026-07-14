from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from research_helper.reports.layout_pdf.backend import (
    BabelDocProvider,
    build_babeldoc_command,
    render_babeldoc_config,
)
from research_helper.reports.layout_pdf.composer import compose_side_by_side
from research_helper.reports.layout_pdf.dual_fidelity import verify_dual_pdf
from research_helper.reports.layout_pdf.fidelity import (
    PdfFidelityError,
    verify_translated_pdf,
)
from research_helper.reports.layout_pdf.numeric_fidelity import protected_numeric_tokens


def _make_pdf(path: Path, *, text_prefix: str, page_count: int = 2) -> Path:
    doc = fitz.open()
    sizes = [(220, 300), (300, 220)]
    for index in range(page_count):
        width, height = sizes[index % len(sizes)]
        page = doc.new_page(width=width, height=height)
        page.insert_text((20, 30), f"{text_prefix} page {index + 1} token 42")
        page.draw_rect(
            fitz.Rect(20, 60, 100, 110),
            color=(0, 0, 0),
            fill=(0.7, 0.85, 1),
        )
        page.draw_line((20, 135), (180, 135), color=(0, 0, 0), width=1)
    doc.save(path)
    doc.close()
    return path


def _copy_with_overlay(source_path: Path, output_path: Path, text: str) -> Path:
    source = fitz.open(source_path)
    translated = fitz.open()
    translated.insert_pdf(source)
    source.close()
    for page in translated:
        font_name = "china-s" if any("\u3400" <= char <= "\u9fff" for char in text) else "helv"
        page.insert_text((20, 52), text, fontname=font_name)
    translated.save(output_path)
    translated.close()
    return output_path


def _gray_samples(page: fitz.Page, clip: fitz.Rect | None = None) -> bytes:
    pixmap = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False, clip=clip)
    return pixmap.samples


def test_babeldoc_config_keeps_api_key_out_of_process_arguments(tmp_path: Path):
    provider = BabelDocProvider(
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key="secret-value",
    )

    config_text = render_babeldoc_config(
        provider=provider,
        output_dir=tmp_path / "out",
        working_dir=tmp_path / "work",
        max_pages=3,
    )
    command = build_babeldoc_command(
        executable=tmp_path / "babeldoc.exe",
        source_pdf=tmp_path / "paper.pdf",
        config_path=tmp_path / "backend.toml",
    )

    assert "secret-value" in config_text
    assert "secret-value" not in " ".join(command)
    assert command == [
        str(tmp_path / "babeldoc.exe"),
        "--config",
        str(tmp_path / "backend.toml"),
        "--files",
        str(tmp_path / "paper.pdf"),
    ]
    assert "no-dual = true" in config_text
    assert "watermark-output-mode = \"no_watermark\"" in config_text
    assert "pages = \"1-3\"" in config_text
    assert "no-auto-extract-glossary = true" in config_text
    assert "only-include-translated-page = true" in config_text
    assert "custom-system-prompt" in config_text
    assert "concise Simplified Chinese" in config_text
    assert "auto_extract_glossary" not in config_text
    assert "only_include_translated_page" not in config_text


def test_composer_keeps_every_source_page_and_left_half_exact(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = _copy_with_overlay(
        source_path,
        tmp_path / "translated.pdf",
        "translated",
    )
    output_path = tmp_path / "dual.pdf"

    compose_side_by_side(source_path, translated_path, output_path)
    report = verify_dual_pdf(source_path, translated_path, output_path)

    source = fitz.open(source_path)
    dual = fitz.open(output_path)
    try:
        assert dual.page_count == source.page_count
        assert report.page_count == source.page_count
        assert report.maximum_left_mae <= 0.25
        for index, source_page in enumerate(source):
            dual_page = dual[index]
            assert dual_page.rect.width == pytest.approx(source_page.rect.width * 2)
            assert dual_page.rect.height == pytest.approx(source_page.rect.height)
            left = fitz.Rect(0, 0, source_page.rect.width, source_page.rect.height)
            assert _gray_samples(source_page) == _gray_samples(dual_page, left)
    finally:
        dual.close()
        source.close()


def test_fidelity_gate_rejects_page_loss(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = _make_pdf(
        tmp_path / "translated.pdf",
        text_prefix="translated",
        page_count=1,
    )

    with pytest.raises(PdfFidelityError, match="page count"):
        verify_translated_pdf(source_path, translated_path)


def test_fidelity_gate_rejects_placeholder_text(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = _copy_with_overlay(
        source_path,
        tmp_path / "translated.pdf",
        "See translation appendix",
    )

    with pytest.raises(PdfFidelityError, match="placeholder"):
        verify_translated_pdf(source_path, translated_path)


def test_fidelity_gate_rejects_new_body_text_overlap(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = tmp_path / "translated.pdf"
    source = fitz.open(source_path)
    translated = fitz.open()
    translated.insert_pdf(source)
    source.close()
    translated[0].insert_text(
        (20, 30),
        "overlapping body text " * 12,
    )
    translated.save(translated_path)
    translated.close()

    with pytest.raises(PdfFidelityError, match="overlapping body text"):
        verify_translated_pdf(source_path, translated_path)


def test_fidelity_gate_allows_wrapped_url_label_overlap(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = tmp_path / "translated.pdf"
    source = fitz.open(source_path)
    translated = fitz.open()
    translated.insert_pdf(source)
    source.close()
    page = translated[0]
    page.insert_text((20, 150), "https://github.com/example/very-long-project-name")
    page.insert_text((20, 150), "可用 available", fontname="china-s")
    translated.save(translated_path)
    translated.close()

    report = verify_translated_pdf(source_path, translated_path)

    assert report.page_count == 2


def test_fidelity_gate_accepts_preserved_geometry_and_nontext(tmp_path: Path):
    source_path = _make_pdf(tmp_path / "source.pdf", text_prefix="source")
    translated_path = _copy_with_overlay(
        source_path,
        tmp_path / "translated.pdf",
        "中文 translated",
    )

    report = verify_translated_pdf(source_path, translated_path)

    assert report.page_count == 2
    assert report.image_occurrences_checked == 0
    assert report.numeric_tokens_checked >= 2


def test_numeric_tokenizer_handles_punctuation_and_adjacent_cjk():
    assert protected_numeric_tokens("Figure 1. 7%提升 RAG2 v2.1") == [
        "1",
        "7%",
        "2",
        "2.1",
    ]
