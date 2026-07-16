from __future__ import annotations

from pathlib import Path

import fitz

from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.reports.layout_pdf.cache_stamp import record_translation_cache
from research_helper.reports.layout_pdf.composer import compose_side_by_side
from research_helper.reports.layout_pdf.pipeline import generate
from research_helper.reports.layout_pdf.url_repair import repair_fragmented_urls

_URI = "https://example.com/research"


def _make_url_pdfs(tmp_path: Path, translated_path: Path) -> Path:
    source_path = tmp_path / "source-url.pdf"
    source = fitz.open()
    source_page = source.new_page(width=220, height=300)
    source_page.insert_text((20, 30), _URI)
    source_page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(20, 18, 190, 34),
            "uri": _URI,
        }
    )
    source.save(source_path)
    source.close()

    translated_path.parent.mkdir(parents=True, exist_ok=True)
    translated = fitz.open()
    page = translated.new_page(width=220, height=300)
    page.insert_textbox(
        fitz.Rect(20, 40, 200, 180),
        "h\nt\nt\np\ns://\nexample.com/research\navailable",
        fontname="cour",
        fontsize=10,
    )
    translated.save(translated_path)
    translated.close()
    return source_path


def test_url_repair_reflows_fragmented_http_prefix(tmp_path: Path):
    translated_path = tmp_path / "fragmented-url.pdf"
    source_path = _make_url_pdfs(tmp_path, translated_path)

    assert repair_fragmented_urls(source_path, translated_path) == 1
    repaired = fitz.open(translated_path)
    try:
        lines = repaired[0].get_text().splitlines()
    finally:
        repaired.close()

    assert "https://" in lines
    assert "example.com/research" in lines
    assert lines[:4] != ["h", "t", "t", "p"]
    assert "available" in lines


def test_generate_rebuilds_cached_dual_after_url_repair(tmp_path: Path):
    paper_dir = tmp_path / "paper"
    translated_path = (
        paper_dir / ".paperwise-layout" / "source-url.no_watermark.zh.mono.pdf"
    )
    source_path = _make_url_pdfs(tmp_path, translated_path)
    output_path = paper_dir / "source-url_paperwise_bilingual_layout.pdf"
    compose_side_by_side(source_path, translated_path, output_path)
    record_translation_cache(source_path, translated_path, selected_pages=1)
    meta = PaperMeta("test", "test", [], "", "", "", [])

    result = generate(source_path, paper_dir, meta)

    source = fitz.open(source_path)
    dual = fitz.open(result)
    try:
        source_width = source[0].rect.width
        right = fitz.Rect(source_width, 0, dual[0].rect.width, dual[0].rect.height)
        lines = dual[0].get_text(clip=right).splitlines()
    finally:
        dual.close()
        source.close()
    assert "https://" in lines
    assert "example.com/research" in lines
    assert lines[:4] != ["h", "t", "t", "p"]


def test_url_repair_repairs_every_repeated_uri_occurrence(tmp_path: Path) -> None:
    source_path = tmp_path / "source-repeated.pdf"
    translated_path = tmp_path / "translated-repeated.pdf"
    source = fitz.open()
    source_page = source.new_page(width=220, height=360)
    for y_position in (30, 210):
        source_page.insert_text((20, y_position), _URI)
        source_page.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(20, y_position - 12, 190, y_position + 4),
                "uri": _URI,
            }
        )
    source.save(source_path)
    source.close()
    translated = fitz.open()
    page = translated.new_page(width=220, height=360)
    broken = "h\nt\nt\np\ns://\nexample.com/research\navailable"
    page.insert_textbox(
        fitz.Rect(20, 30, 200, 170),
        broken,
        fontname="cour",
        fontsize=10,
    )
    page.insert_textbox(
        fitz.Rect(20, 200, 200, 340),
        broken,
        fontname="cour",
        fontsize=10,
    )
    translated.save(translated_path)
    translated.close()

    repaired_count = repair_fragmented_urls(source_path, translated_path)

    repaired = fitz.open(translated_path)
    try:
        lines = repaired[0].get_text().splitlines()
        uri_links = [
            link
            for link in repaired[0].get_links()
            if link.get("uri") == _URI
        ]
    finally:
        repaired.close()
    assert repaired_count == 2
    assert lines.count("https://") == 2
    assert len(uri_links) == 2


def test_url_repair_keeps_trailing_word_spacing(tmp_path: Path) -> None:
    source_path = tmp_path / "source-spacing.pdf"
    translated_path = tmp_path / "translated-spacing.pdf"
    source = fitz.open()
    page = source.new_page(width=220, height=300)
    page.insert_text((20, 30), _URI)
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(20, 18, 190, 34),
            "uri": _URI,
        }
    )
    source.save(source_path)
    source.close()
    translated = fitz.open()
    page = translated.new_page(width=220, height=300)
    page.insert_textbox(
        fitz.Rect(20, 40, 200, 180),
        "h\nt\nt\np\ns://\nexample.com/research\navailable          now",
        fontname="cour",
        fontsize=10,
    )
    translated.save(translated_path)
    translated.close()

    repair_fragmented_urls(source_path, translated_path)

    repaired = fitz.open(translated_path)
    try:
        words = {
            str(word[4]): fitz.Rect(word[:4])
            for word in repaired[0].get_text("words")
            if str(word[4]) in {"available", "now"}
        }
    finally:
        repaired.close()
    assert words["now"].x0 - words["available"].x1 > 30
