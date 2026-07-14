from __future__ import annotations

from pathlib import Path

import fitz

from research_helper.reports.layout_pdf.links import (
    insert_link_specs,
    page_link_specs,
)


class PdfCompositionError(RuntimeError):
    pass


def compose_side_by_side(
    source_pdf: Path,
    translated_pdf: Path,
    output_pdf: Path,
    *,
    max_pages: int | None = None,
) -> Path:
    source = fitz.open(source_pdf)
    translated = fitz.open(translated_pdf)
    output = fitz.open()
    try:
        selected_pages = min(source.page_count, max_pages) if max_pages else source.page_count
        if translated.page_count != selected_pages:
            raise PdfCompositionError(
                f"page count mismatch: source selection has {selected_pages}, "
                f"translation has {translated.page_count}"
            )
        right_destinations = tuple(source[index].rect.width for index in range(selected_pages))
        left_destinations = (0.0,) * selected_pages

        for index in range(selected_pages):
            source_page = source[index]
            translated_page = translated[index]
            _require_matching_page_geometry(index, source_page, translated_page)
            source_rect = source_page.rect
            translated_rect = translated_page.rect
            target = output.new_page(
                width=source_rect.width + translated_rect.width,
                height=source_rect.height,
            )
            left = fitz.Rect(0, 0, source_rect.width, source_rect.height)
            right = fitz.Rect(
                source_rect.width,
                0,
                source_rect.width + translated_rect.width,
                source_rect.height,
            )
            target.show_pdf_page(left, source, index, keep_proportion=True)
            target.show_pdf_page(right, translated, index, keep_proportion=True)
        for index in range(selected_pages):
            source_page = source[index]
            translated_page = translated[index]
            source_width = source_page.rect.width
            target = output[index]
            source_links = page_link_specs(
                source_page,
                rectangle_x_offset=0.0,
                selected_pages=selected_pages,
                destination_x_offsets=left_destinations,
            )
            translated_links = page_link_specs(
                translated_page,
                rectangle_x_offset=source_width,
                selected_pages=selected_pages,
                destination_x_offsets=right_destinations,
            )
            insert_link_specs(target, source_links + translated_links)

        metadata = dict(source.metadata)
        metadata["producer"] = "Paperwise layout-preserving backend"
        metadata["title"] = (
            f"{metadata.get('title') or Path(source_pdf).stem} - Paperwise bilingual"
        )
        output.set_metadata(metadata)
        toc = [entry for entry in source.get_toc() if entry[2] <= selected_pages]
        if toc:
            output.set_toc(toc)
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        output.save(output_pdf, garbage=4, deflate=True, deflate_fonts=True)
    finally:
        output.close()
        translated.close()
        source.close()
    return output_pdf


def _require_matching_page_geometry(
    page_index: int,
    source_page: fitz.Page,
    translated_page: fitz.Page,
) -> None:
    tolerance = 0.25
    if (
        abs(source_page.rect.width - translated_page.rect.width) > tolerance
        or abs(source_page.rect.height - translated_page.rect.height) > tolerance
    ):
        raise PdfCompositionError(
            f"page {page_index + 1} geometry mismatch: "
            f"source={source_page.rect}, translated={translated_page.rect}"
        )
