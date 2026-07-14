from __future__ import annotations

from pathlib import Path

import fitz

from research_helper.reports.layout_pdf.composer import compose_side_by_side
from research_helper.reports.layout_pdf.dual_fidelity import verify_dual_pdf
from research_helper.reports.layout_pdf.links import LinkType, page_link_specs

_URI = "https://example.com/paper"


def _write_source_with_named_destination(path: Path) -> Path:
    document = fitz.open()
    document.new_page(width=220, height=300)
    document.new_page(width=220, height=300)
    document[0].insert_text((20, 30), "source page 1 token 42")
    document[1].insert_text((20, 30), "source page 2 token 42")

    destination_names = document.get_new_xref()
    page_xref = document[1].xref
    document.update_object(
        destination_names,
        f"<< /Names [(chapter-two) [{page_xref} 0 R /XYZ 20 270 0]] >>",
    )
    names = document.get_new_xref()
    document.update_object(names, f"<< /Dests {destination_names} 0 R >>")
    document.xref_set_key(document.pdf_catalog(), "Names", f"{names} 0 R")

    page = document[0]
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(20, 40, 150, 55),
            "uri": _URI,
        }
    )
    page.insert_link(
        {
            "kind": fitz.LINK_NAMED,
            "from": fitz.Rect(20, 60, 100, 75),
            "name": "chapter-two",
        }
    )
    document.save(path)
    document.close()
    return path


def _write_translation_with_uri(path: Path) -> Path:
    document = fitz.open()
    for index in range(2):
        page = document.new_page(width=220, height=300)
        page.insert_text(
            (20, 30),
            f"中文译文 page {index + 1} token 42",
            fontname="china-s",
        )
    document[0].insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(20, 90, 150, 105),
            "uri": _URI,
        }
    )
    document.save(path)
    document.close()
    return path


def test_composer_preserves_functional_source_and_translated_links(
    tmp_path: Path,
) -> None:
    source = _write_source_with_named_destination(tmp_path / "source.pdf")
    translated = _write_translation_with_uri(tmp_path / "translated.pdf")
    dual = tmp_path / "dual.pdf"

    compose_side_by_side(source, translated, dual)
    verify_dual_pdf(source, translated, dual)

    document = fitz.open(dual)
    try:
        links = document[0].get_links()
    finally:
        document.close()
    left_uri = [
        link
        for link in links
        if link.get("kind") == fitz.LINK_URI
        and link.get("uri") == _URI
        and fitz.Rect(link["from"]).x0 < 220
    ]
    right_uri = [
        link
        for link in links
        if link.get("kind") == fitz.LINK_URI
        and link.get("uri") == _URI
        and fitz.Rect(link["from"]).x0 >= 220
    ]
    internal = [link for link in links if link.get("kind") == fitz.LINK_GOTO]

    assert len(left_uri) == 1
    assert len(right_uri) == 1
    assert fitz.Rect(right_uri[0]["from"]).x0 == 240
    assert len(internal) == 1
    assert internal[0]["page"] == 1


def test_launch_action_with_web_url_is_normalized_to_uri() -> None:
    class LaunchPage:
        def get_links(self) -> list[dict]:
            return [
                {
                    "kind": fitz.LINK_LAUNCH,
                    "from": fitz.Rect(20, 40, 150, 55),
                    "file": f'"{_URI}"',
                }
            ]

    specs = page_link_specs(
        LaunchPage(),
        rectangle_x_offset=0.0,
        selected_pages=1,
        destination_x_offsets=(0.0,),
    )

    assert len(specs) == 1
    assert specs[0].link_type is LinkType.URI
    assert specs[0].uri == _URI
