from __future__ import annotations

from pathlib import Path

import fitz

from research_helper.readers.arxiv_reader import PaperMeta
from research_helper.reports.layout_pdf.cache_stamp import record_translation_cache
from research_helper.reports.layout_pdf.composer import compose_side_by_side
from research_helper.reports.layout_pdf.pipeline import generate
from research_helper.reports.layout_pdf.text_repair import repair_latin_typography


def _write_source(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page(width=400, height=200)
    page.insert_text(
        (20, 30),
        "Bernal Jiménez Gutiérrez <jimenezgutierrez.1@osu.edu>",
        fontname="tiro",
        fontsize=10,
    )
    page.insert_text((20, 70), "Gutiérrez", fontname="tiro", fontsize=10)
    latin_width = fitz.get_text_length("Gutiérrez", fontname="tiro", fontsize=10)
    page.insert_text(
        (20 + latin_width, 70),
        "等人",
        fontname="china-s",
        fontsize=10,
    )
    document.save(path)
    document.close()
    return path


def _write_broken_translation(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((20, 30), "中文译文", fontname="china-s", fontsize=10)
    page.insert_text(
        (20, 55),
        "Bernal Jim´enez Guti´errez <jimenezgutier- rez.1@osu.edu>",
        fontname="tiro",
        fontsize=10,
    )
    page.insert_text((20, 90), "Guti´errez", fontname="tiro", fontsize=10)
    latin_width = fitz.get_text_length("Guti´errez", fontname="tiro", fontsize=10)
    page.insert_text(
        (20 + latin_width, 90),
        "等人",
        fontname="china-s",
        fontsize=10,
    )
    document.save(path)
    document.close()
    return path


def _write_legacy_repaired_translation(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((20, 30), "中文译文", fontname="china-s", fontsize=10)
    page.insert_text((20, 55), "Jiménez", fontname="tibo", fontsize=10)
    page.insert_text((70, 55), "Gutiérrez", fontname="tibo", fontsize=10)
    page.insert_text((20, 90), "Jiménez", fontname="tiro", fontsize=8)
    page.insert_text((57, 90), "Gutiérrez", fontname="tiro", fontsize=8)
    page.insert_text(
        (100, 90),
        "<jimenezgutierrez.1@osu.edu>",
        fontname="tiro",
        fontsize=8,
    )
    page.insert_text(
        (20, 125),
        "Gutiérrez等人,",
        fontname="china-s",
        fontsize=6,
    )
    page.insert_text((89, 125), "2024", fontname="tiro", fontsize=9)
    page.insert_text((107, 125), "）后文", fontname="china-s", fontsize=9)
    document.save(path)
    document.close()
    return path


def test_generate_repairs_detached_accents_and_split_email(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.pdf")
    paper_dir = tmp_path / "paper"
    translated = _write_broken_translation(
        paper_dir / ".paperwise-layout" / "source.no_watermark.zh.mono.pdf"
    )
    output = paper_dir / "source_paperwise_bilingual_layout.pdf"
    compose_side_by_side(source, translated, output)
    record_translation_cache(source, translated, selected_pages=1)
    meta = PaperMeta("test", "test", [], "", "", "", [])

    result = generate(source, paper_dir, meta)

    document = fitz.open(result)
    try:
        right = fitz.Rect(400, 0, 800, 200)
        translated_text = document[0].get_text(clip=right)
        cjk_sizes = [
            float(span["size"])
            for block in document[0].get_text("dict", clip=right).get("blocks", [])
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if "等人" in str(span.get("text", ""))
        ]
    finally:
        document.close()
    assert "Jiménez Gutiérrez" in translated_text
    assert "jimenezgutierrez.1@osu.edu" in translated_text
    assert "Gutiérrez等人" in translated_text.replace("\n", "")
    assert cjk_sizes and min(cjk_sizes) >= 9.5
    assert "´" not in translated_text
    assert "gutier- rez" not in translated_text


def test_repair_migrates_split_legacy_typography(tmp_path: Path) -> None:
    translated = _write_legacy_repaired_translation(tmp_path / "legacy.pdf")

    repair_count = repair_latin_typography(translated)
    repeated_repair_count = repair_latin_typography(translated)

    document = fitz.open(translated)
    try:
        translated_text = document[0].get_text()
        mixed_sizes = [
            float(span["size"])
            for block in document[0].get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if "等人" in str(span.get("text", ""))
        ]
    finally:
        document.close()
    assert repair_count == 3
    assert repeated_repair_count == 0
    assert "Jiménez Gutiérrez" in translated_text
    assert "Jiménez Gutiérrez <jimenezgutierrez.1@osu.edu>" in translated_text
    assert "Gutiérrez等人," in translated_text.replace("\n", "")
    assert "2024" in translated_text
    assert mixed_sizes and min(mixed_sizes) >= 8.9
