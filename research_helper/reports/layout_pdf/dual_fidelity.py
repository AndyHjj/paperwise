from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from research_helper.reports.layout_pdf.fidelity import PdfFidelityError
from research_helper.reports.layout_pdf.links import (
    link_fingerprints,
    page_link_specs,
)


@dataclass(frozen=True, slots=True)
class DualFidelityReport:
    page_count: int
    image_occurrences_checked: int
    maximum_left_mae: float


class DualPdfStructureError(PdfFidelityError):
    pass


def verify_dual_pdf(
    source_pdf: Path,
    translated_pdf: Path,
    dual_pdf: Path,
    *,
    max_pages: int | None = None,
) -> DualFidelityReport:
    source = fitz.open(source_pdf)
    translated = fitz.open(translated_pdf)
    dual = fitz.open(dual_pdf)
    try:
        expected_pages = min(source.page_count, max_pages) if max_pages else source.page_count
        if translated.page_count != expected_pages or dual.page_count != expected_pages:
            raise DualPdfStructureError(
                "dual page count mismatch: "
                f"source selection={expected_pages}, translation={translated.page_count}, "
                f"dual={dual.page_count}"
            )
        issues: list[str] = []
        image_count = 0
        maximum_left_mae = 0.0
        right_destinations = tuple(source[index].rect.width for index in range(expected_pages))
        left_destinations = (0.0,) * expected_pages
        for index in range(expected_pages):
            source_page = source[index]
            translated_page = translated[index]
            dual_page = dual[index]
            source_width = source_page.rect.width
            expected_width = source_width + translated_page.rect.width
            if (
                abs(dual_page.rect.width - expected_width) > 0.25
                or abs(dual_page.rect.height - source_page.rect.height) > 0.25
            ):
                raise DualPdfStructureError(
                    f"page {index + 1} has incorrect dual-page geometry"
                )
            left = fitz.Rect(0, 0, source_width, source_page.rect.height)
            right = fitz.Rect(source_width, 0, expected_width, source_page.rect.height)
            left_metrics = _raster_metrics(source_page, dual_page, left)
            right_metrics = _raster_metrics(translated_page, dual_page, right)
            maximum_left_mae = max(maximum_left_mae, left_metrics[0])
            if left_metrics[0] > 0.25 or left_metrics[1] > 0.01:
                issues.append(
                    f"page {index + 1} left half changed "
                    f"(MAE={left_metrics[0]:.3f}, changed={left_metrics[1]:.3%})"
                )
            if right_metrics[0] > 0.25 or right_metrics[1] > 0.01:
                issues.append(
                    f"page {index + 1} translated half changed during composition "
                    f"(MAE={right_metrics[0]:.3f}, changed={right_metrics[1]:.3%})"
                )
            image_count += _check_dual_images(
                index,
                source_page,
                translated_page,
                dual_page,
                source_width,
                issues,
            )
            expected_links = link_fingerprints(
                page_link_specs(
                    source_page,
                    rectangle_x_offset=0.0,
                    selected_pages=expected_pages,
                    destination_x_offsets=left_destinations,
                )
                + page_link_specs(
                    translated_page,
                    rectangle_x_offset=source_width,
                    selected_pages=expected_pages,
                    destination_x_offsets=right_destinations,
                )
            )
            actual_links = link_fingerprints(
                page_link_specs(
                    dual_page,
                    rectangle_x_offset=0.0,
                    selected_pages=expected_pages,
                    destination_x_offsets=left_destinations,
                )
            )
            missing_links = expected_links - actual_links
            added_links = actual_links - expected_links
            if missing_links or added_links:
                issues.append(
                    f"page {index + 1} link annotations changed "
                    f"({sum(missing_links.values())} missing, "
                    f"{sum(added_links.values())} unexpected)"
                )
        if issues:
            raise PdfFidelityError("Dual PDF fidelity check failed:\n- " + "\n- ".join(issues))
        return DualFidelityReport(expected_pages, image_count, maximum_left_mae)
    finally:
        dual.close()
        translated.close()
        source.close()


def _raster_metrics(
    reference_page: fitz.Page,
    dual_page: fitz.Page,
    clip: fitz.Rect,
) -> tuple[float, float]:
    reference = reference_page.get_pixmap(colorspace=fitz.csGRAY, alpha=False).samples
    candidate = dual_page.get_pixmap(
        colorspace=fitz.csGRAY,
        alpha=False,
        clip=clip,
    ).samples
    if len(reference) != len(candidate):
        return 255.0, 1.0
    total_difference = 0
    changed = 0
    for expected, actual in zip(reference, candidate):
        difference = abs(expected - actual)
        total_difference += difference
        changed += difference > 8
    count = max(1, len(reference))
    return total_difference / count, changed / count


def _check_dual_images(
    page_index: int,
    source_page: fitz.Page,
    translated_page: fitz.Page,
    dual_page: fitz.Page,
    right_offset: float,
    issues: list[str],
) -> int:
    dual_images = list(dual_page.get_image_info(xrefs=True))
    expected = [
        (image, 0.0) for image in source_page.get_image_info(xrefs=True)
    ] + [
        (image, right_offset) for image in translated_page.get_image_info(xrefs=True)
    ]
    unused = list(range(len(dual_images)))
    for image, offset in expected:
        match = next(
            (
                position
                for position in unused
                if _matches_image(image, dual_images[position], offset)
            ),
            None,
        )
        if match is None:
            issues.append(
                f"page {page_index + 1} lost or moved a dual image at {image.get('bbox')}"
            )
        else:
            unused.remove(match)
    for position in unused:
        issues.append(
            f"page {page_index + 1} added a dual image at "
            f"{dual_images[position].get('bbox')}"
        )
    return len(expected)


def _matches_image(source: dict, target: dict, x_offset: float) -> bool:
    if source.get("digest") != target.get("digest"):
        return False
    if source.get("width") != target.get("width") or source.get("height") != target.get("height"):
        return False
    source_bbox = fitz.Rect(source["bbox"])
    source_bbox.x0 += x_offset
    source_bbox.x1 += x_offset
    target_bbox = fitz.Rect(target["bbox"])
    return all(abs(left - right) <= 0.25 for left, right in zip(source_bbox, target_bbox))
